"""Chromium + loopback Web/API; synthetic audio, mocked inference, owned cleanup."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect
from run_local import stop_owned_group


def main() -> None:
    """Exercise product HTTP boundaries without audio/model transfer or inference."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-python',type=Path,required=True)
    parser.add_argument('--result-dir',type=Path,required=True)
    args=parser.parse_args();args.backend_python=args.backend_python.absolute()
    root=Path(__file__).resolve().parents[1];result=args.result_dir.resolve();result.mkdir()
    for port in (8320,4320):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:raise RuntimeError('Task port occupied; owner untouched')
    bucket=result/'bucket';bucket.mkdir()
    env={**os.environ,'FILE_BUCKET_PATH':str(bucket),'PYTHONPATH':str(root/'backend'),'PYTHONDONTWRITEBYTECODE':'1','API_PROXY_TARGET':'http://127.0.0.1:8320','WEB_PORT':'4320'}
    launcher=result/'fixture-api.py'
    launcher.write_text('''import runpy,time,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest,uvicorn
from src.api import transcriptions as song,muscriptor_trials as trial
fixture=runpy.run_path(str(Path(os.environ['PYTHONPATH'])/'tests/unit/test_transcriptions_api.py'))
patch=pytest.MonkeyPatch();fixture['stages'](patch);original=trial._run_stage
patch.setattr(trial,'_load_deployment',lambda:SimpleNamespace(model_dump=lambda **kw:{'synthetic':True,'model_inference':False}))
def stage(name,deploy,folder,source,slot):
    if name=='transcribe':(folder/name).mkdir()
    for i in range(6):
        time.sleep(.15);song._cancelled(folder)
        if name=='transcribe':trial._write_json(folder/name/'progress.json',dict(completed=i//2,total=3))
    request=json.loads((folder/'request.json').read_text())
    import soundfile as sf
    samples,_=sf.read(str(folder/'selection.wav'))
    if name=='transcribe' and samples.max()>.15 and not request['retry_of']:
        output=folder/name;output.mkdir(exist_ok=True);(output/'notes.json').write_text('[{"onset":.3,"offset":.2}]')
        raise trial.TrialFailure('invalid_model_output','SYNTHETIC invalid stream rejected; raw retained without repair.')
    return original(name,deploy,folder,source,slot)
patch.setattr(trial,'_run_stage',stage)
bucket=Path(os.environ['FILE_BUCKET_PATH'])
(bucket/'synthetic.wav').write_bytes(fixture['audio'](12))
(bucket/'synthetic-fail.wav').write_bytes(fixture['audio'](12,.2))
from src.api.main import app
uvicorn.run(app,host='127.0.0.1',port=8320,log_level='info')
''')
    logs=[];processes=[];checks=[];errors=[]
    def start(command,cwd,name,process_env=None):
        log=(result/name).open('w');logs.append(log)
        p=subprocess.Popen(command,cwd=cwd,env=env if process_env is None else process_env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(p);return p
    def start_api():return start([str(args.backend_python),str(launcher)],root/'backend',f'api-{len(processes)}.log')
    def stop(p):
        stop_owned_group(p)
    def ready(url):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(url,timeout=1) as response:
                    if response.status==200:return
            except OSError:time.sleep(.1)
        raise RuntimeError('Server did not become ready: '+url)
    def passed(name):checks.append(name);print('PASS '+name,flush=True)
    try:
        api=start_api();start(['npm','run','preview','--','--host','127.0.0.1','--strictPort'],root/'frontend','web.log')
        base='http://127.0.0.1:4320';ready(base+'/api/v1/transcriptions/health')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1180,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base);expect(page.get_by_role('heading',name='音源からベースTABを作る')).to_be_visible()
            expect(page.get_by_text('Web: 接続済み',exact=False)).to_contain_text('配置済み')
            expect(page.get_by_role('button',name='2. 分離して採譜する')).to_be_disabled();passed('primary MuScriptor, rights guard, asset readiness')
            page.get_by_label('音源 WAV/FLAC').set_input_files(bucket/'synthetic.wav');page.get_by_label('使用権のある音源',exact=False).check()
            posts=[];page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/transcriptions') else None)
            page.get_by_role('button',name='2. 分離して採譜する').dblclick()
            page.wait_for_function("sessionStorage.getItem('stem2tab.song.request') !== null")
            identifier=page.evaluate("sessionStorage.getItem('stem2tab.song.request')");page.reload()
            expect(page.get_by_text('処理ID: '+identifier)).to_be_visible()
            expect(page.get_by_text('採譜区間',exact=False)).to_be_visible(timeout=10000)
            link=lambda:page.get_by_role('link',name='3. 試聴・五線譜・TAB・保存へ')
            expect(link()).to_be_visible(timeout=20000);assert len(posts)==1
            passed('actual multipart upload, duplicate click, reload, completion')
            folder=bucket/'muscriptor-trials/songs'/identifier
            assert (folder/'original.audio').read_bytes()==(bucket/'synthetic.wav').read_bytes()
            assert page.request.get(base+f'/api/v1/transcriptions/{identifier}/files/performance.mid').status==200
            page.evaluate("document.body.insertAdjacentHTML('afterbegin','<strong>SYNTHETIC TEST — INFERENCE MOCKED</strong>')")
            page.screenshot(path=str(result/'workflow-synthetic.png'),full_page=True);passed('original bytes/raw MIDI retention')
            link().click();expect(page.locator('.midi-trial-score svg').first).to_be_visible(timeout=45000)
            expect(page.get_by_role('button',name='再生',exact=True)).to_be_enabled(timeout=45000)
            page.get_by_role('button',name='再生',exact=True).click();page.wait_for_timeout(200);page.get_by_role('button',name='停止',exact=True).click()
            passed('staff/TAB and real playback/stop of synthetic notes')
            page.goto(base);page.get_by_role('button',name='別の音源を選ぶ').click()
            page.get_by_label('音源 WAV/FLAC').set_input_files(bucket/'synthetic.wav');page.get_by_label('使用権のある音源',exact=False).check()
            page.get_by_label('処理範囲').select_option('range');page.get_by_label('開始秒').fill('1');page.get_by_label('終了秒').fill('11')
            page.get_by_role('button',name='2. 分離して採譜する').click();expect(page.get_by_role('button',name='処理を取り消す')).to_be_visible()
            cancelled=page.evaluate("sessionStorage.getItem('stem2tab.song.request')");page.get_by_role('button',name='処理を取り消す').dblclick()
            retry=lambda:page.get_by_role('button',name='新しい試行として再実行（検証済み分離を再利用）')
            expect(retry()).to_be_visible(timeout=10000);assert page.request.get(base+f'/api/v1/transcriptions/{cancelled}').json()['status']=='cancelled'
            passed('range selection, repeat cancel, terminal stop')
            retry().click();expect(link()).to_be_visible(timeout=20000)
            assert page.evaluate("sessionStorage.getItem('stem2tab.song.request')")!=cancelled
            assert (bucket/'muscriptor-trials/songs'/cancelled/'selection.wav').exists();passed('new-ID retry retains cancelled source')
            stop(api);proxy=page.request.get(base+'/api/v1/transcriptions/health')
            assert proxy.status==503 and proxy.json()['detail']['code']=='api_unavailable'
            page.get_by_role('button',name='接続を再確認').click();expect(page.get_by_role('alert')).to_contain_text('APIに接続できません')
            passed('actual API stop: JSON 503 and service error')
            api=start_api();ready(base+'/api/v1/transcriptions/health');page.get_by_role('button',name='接続を再確認').click()
            expect(page.get_by_text('Web: 接続済み',exact=False)).to_contain_text('配置済み');passed('actual API restart and Web recovery')
            page.get_by_role('button',name='別の音源を選ぶ').click();page.get_by_label('音源 WAV/FLAC').set_input_files(bucket/'synthetic-fail.wav')
            page.get_by_role('button',name='2. 分離して採譜する').click()
            expect(page.get_by_text('SYNTHETIC invalid stream rejected; raw retained without repair.')).to_be_visible(timeout=20000)
            failed=page.evaluate("sessionStorage.getItem('stem2tab.song.request')");raw=bucket/'muscriptor-trials/songs'/failed/'transcribe/notes.json';before=raw.read_bytes()
            retry().click();expect(link()).to_be_visible(timeout=20000);retried=page.evaluate("sessionStorage.getItem('stem2tab.song.request')")
            assert raw.read_bytes()==before and (bucket/'muscriptor-trials/songs'/retried/'separate/reused.json').exists()
            passed('invalid raw failure, validated separation reuse, original diagnostics retained')
            stop(api)
            unconfigured={key:value for key,value in env.items() if key!='STEM2TAB_MUSCRIPTOR_DEPLOYMENT'}
            api=start([str(args.backend_python),'-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port','8320'],root/'backend','api-unconfigured.log',unconfigured)
            ready(base+'/api/v1/transcriptions/health');page.get_by_role('button',name='接続を再確認').click()
            expect(page.get_by_text('Web: 接続済み',exact=False)).to_contain_text('利用できません')
            expect(page.get_by_text('MuScriptor small local trial is not configured; no assets will be downloaded.')).to_be_visible()
            page.get_by_role('button',name='別の音源を選ぶ').click()
            expect(page.get_by_role('button',name='2. 分離して採譜する')).to_be_disabled()
            assert page.request.get(base+f'/api/v1/transcriptions/{retried}/files/performance.mid').status==200
            passed('actual unconfigured API reports missing assets; execution disabled, saved raw output still accessible')
            assert errors==[],errors;passed('no uncaught browser errors');browser.close()
    finally:
        for p in reversed(processes):stop(p)
        for log in logs:log.close()
        ports={}
        for port in (8320,4320):
            with socket.socket() as probe:ports[str(port)]=probe.connect_ex(('127.0.0.1',port))==0
        (result/'results.json').write_text(json.dumps(dict(synthetic_only=True,inference='mocked; no model/GPU',checks=checks,page_errors=errors,ports_listening_after=ports),ensure_ascii=False,indent=2)+'\n')
        assert not any(ports.values()),ports


if __name__=='__main__':main()
