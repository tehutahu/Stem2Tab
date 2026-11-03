"""Synthetic product workflow checks: no assets, model inference or CUDA."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import mido
import numpy as np
import pytest
import soundfile as sf

from src.api import transcriptions as song, muscriptor_trials as trial
from src.api.main import app
from src.core.config import settings


def audio(seconds=12, value=0):
    stream = io.BytesIO()
    sf.write(stream,np.full(int(8000*seconds),value,dtype=np.float32),8000,format='WAV',subtype='FLOAT')
    return stream.getvalue()


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path); trial._SHUTDOWN.clear()
    monkeypatch.setattr(trial,'_load_deployment',lambda:SimpleNamespace(model_dump=lambda **kw:{'fixture':'no-model'}))
    monkeypatch.setattr(song,'_launch',lambda folder,deploy,slot,reuse:song._run(folder,deploy,slot,reuse))
    return TestClient(app)


def post(client,data=None,identifier=None,**fields):
    return client.post('/api/v1/transcriptions',files={'file':('synthetic.wav',audio() if data is None else data)},
        data={'request_id':identifier or str(uuid4()),'rights_confirmed':'true',**fields})


def stages(monkeypatch,fail=False):
    seen=[]
    def run(stage,deploy,folder,source,slot):
        seen.append(stage); output=folder/stage; output.mkdir(exist_ok=True)
        if stage=='separate': (output/'bass.wav').write_bytes(source.read_bytes())
        else:
            (output/'notes.json').write_text('[{"raw":true}]')
            if fail:raise trial.TrialFailure('invalid_model_output','Synthetic invalid stream; not repaired.')
            midi=mido.MidiFile(ticks_per_beat=10000)
            track=mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=1000000,time=0),mido.Message('program_change',program=33,time=0)])
            tick=0
            for pitch,onset,offset in [(28,4.9,5.1),(28,5.1,5.2),(33,9.9,10.1)]:
                a,b=round(onset*10000),round(offset*10000)
                track.extend([mido.Message('note_on',note=pitch,velocity=100,time=a-tick),mido.Message('note_off',note=pitch,time=b-a)]);tick=b
            midi.tracks.append(track);midi.save(output/'performance.mid')
        result={'status':'completed','stage':stage}
        if stage=='transcribe':result['performance_midi_sha256']=trial._sha(output/'performance.mid')
        trial._write_json(output/'result.json',result)
        return result
    monkeypatch.setattr(trial,'_run_stage',run)
    return seen


def terminal(client,response):
    assert response.status_code==202,response.text
    return client.get('/api/v1/transcriptions/'+response.json()['job_id']).json()


def test_failed_model_output_explains_retained_timing_without_changing_status(client):
    from src.pipelines.muscriptor_decoder import inspect_decoder_integrity
    identifier=str(uuid4());folder=song._folder(identifier,exists=False);folder.mkdir()
    (folder/'transcribe').mkdir()
    integrity=inspect_decoder_integrity([dict(onset=.1,offset=.2),dict(onset=.3,offset=.3)],[])
    (folder/'transcribe/decoder-integrity.json').write_text(integrity.model_dump_json())
    song._state(folder,status='failed',stage='invalid_model_output',
                error=trial.TrialError(code='invalid_model_output',message='Generic local stage failure'))
    before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    result=client.get('/api/v1/transcriptions/'+identifier).json()
    assert result['status']=='failed' and result['stage']=='invalid_model_output'
    assert result['error']['code']=='invalid_model_output'
    assert '出力2音中' in result['error']['message'] and '同時刻の音が1件' in result['error']['message']
    assert result['midi_trial_id'] is None
    assert before=={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def test_unreadable_auxiliary_diagnostic_keeps_original_failure(client):
    identifier=str(uuid4());folder=song._folder(identifier,exists=False);folder.mkdir()
    (folder/'transcribe').mkdir();(folder/'transcribe/decoder-integrity.json').write_text('{')
    song._state(folder,status='failed',stage='invalid_model_output',
                error=trial.TrialError(code='invalid_model_output',message='Original failure'))
    response=client.get('/api/v1/transcriptions/'+identifier)
    assert response.status_code==200 and response.json()['error']['message']=='Original failure'


def test_long_input_original_boundary_notes_and_exports(client,monkeypatch):
    seen=stages(monkeypatch); original=audio(); state=terminal(client,post(client,original))
    assert state['status']=='completed' and state['accuracy_evaluated'] is False
    assert seen==['separate','transcribe']; folder=song._folder(state['job_id'])
    assert (folder/'original.audio').read_bytes()==original
    assert json.loads((folder/'request.json').read_text())['selection']['end_frame']==96000
    saved=client.get('/api/v1/midi-trials/'+state['midi_trial_id']).json()
    assert saved['input_note_count']==3 and saved['gp5']['status']=='not_requested'
    for name in ['original.audio','selection.wav','bass.wav','performance.mid']:
        assert client.get(f"/api/v1/transcriptions/{state['job_id']}/files/{name}").status_code==200


def test_zero_duration_worker_output_finishes_as_review_without_score_import(client,monkeypatch):
    from src.pipelines import model_output_review as review, muscriptor_worker as worker
    from src.pipelines.muscriptor_decoder import inspect_decoder_integrity
    seen=[]
    def run(stage,deploy,folder,source,slot):
        seen.append(stage);output=folder/stage;output.mkdir()
        if stage=='separate':
            (output/'bass.wav').write_bytes(source.read_bytes())
            return dict(status='completed',stage=stage)
        notes=[dict(index=i,pitch=40,program=33,instrument='electric_bass',is_drum=False,onset=a,offset=b)
            for i,(a,b) in enumerate([(.1,.5),(.5,.5),(.5,1.)])]
        tokens=[]
        trial._write_json(output/'notes.json',notes)
        (output/'tokens.jsonl').write_text('')
        (output/'events.jsonl').write_text('{}\n')
        trial._write_json(output/'decoder-integrity.json',inspect_decoder_integrity(notes,tokens).model_dump(mode='json'))
        worker._literal_midi(output/'model-events.mid',notes,preserve_zero_duration=True)
        review.write_manifest(output,output/'model-events.mid',12.,'needs_review')
        result=dict(status='needs_review',assets_verified_after=True,
            output_review_manifest_sha256=review.digest(output/'output-review.json'))
        trial._write_json(output/'result.json',result)
        return result
    monkeypatch.setattr(trial,'_run_stage',run)
    monkeypatch.setattr(song.midi_trials,'import_midi_bytes',lambda *a,**k:pytest.fail('No automatic omission or score import'))
    state=terminal(client,post(client))
    assert state['status']=='needs_review' and state['review']['raw_note_count']==3
    assert state['midi_trial_id'] is None and seen==['separate','transcribe']
    assert 'model-events.mid' in state['available_files']
    assert client.post('/api/v1/transcriptions/'+state['job_id']+'/retry',data={'request_id':str(uuid4())}).status_code==409


def test_same_bytes_resend_once_different_bytes_conflict(client,monkeypatch):
    seen=stages(monkeypatch); identifier=str(uuid4()); data=audio()
    first=post(client,data,identifier);repeat=post(client,data,identifier)
    assert first.json()['job_id']==repeat.json()['job_id'] and repeat.status_code==202 and len(seen)==2
    assert post(client,audio(value=.1),identifier).status_code==409


@pytest.mark.parametrize(('start','end'),[(2,2),(-1,2),(0,13),(float('nan'),2),(3,1)])
def test_invalid_selection_never_runs(client,monkeypatch,start,end):
    seen=stages(monkeypatch);assert post(client,start_seconds=start,end_seconds=end).status_code==422
    assert seen==[]


def test_sample_aligned_selection_and_limit(client,monkeypatch):
    stages(monkeypatch);state=terminal(client,post(client,start_seconds=1.234,end_seconds=11.234))
    selection=json.loads((song._folder(state['job_id'])/'request.json').read_text())['selection']
    assert selection['start_frame']==9872 and selection['end_frame']==89872
    assert selection['note_time_origin']=='selection_start'
    assert post(client,audio(601)).status_code==422


def test_retry_reuses_verified_separation_retains_failure(client,monkeypatch):
    stages(monkeypatch,True);state=terminal(client,post(client));old=song._folder(state['job_id'])
    raw=(old/'transcribe/notes.json').read_bytes()
    assert state['status']=='failed' and state['error']['code']=='invalid_model_output'
    seen=stages(monkeypatch)
    response=client.post(f"/api/v1/transcriptions/{state['job_id']}/retry",data={'request_id':str(uuid4())})
    done=terminal(client,response)
    assert done['status']=='completed' and (song._folder(done['job_id'])/'separate/reused.json').is_file()
    assert (old/'transcribe/notes.json').read_bytes()==raw and seen==['transcribe']


def test_tampered_checkpoint_not_reused(client,monkeypatch):
    stages(monkeypatch,True);state=terminal(client,post(client))
    (song._folder(state['job_id'])/'separate/bass.wav').write_bytes(b'altered');seen=stages(monkeypatch)
    response=client.post(f"/api/v1/transcriptions/{state['job_id']}/retry",data={'request_id':str(uuid4())})
    assert terminal(client,response)['status']=='completed' and seen==['separate','transcribe']


def test_cancel_pending_and_terminal_idempotency(client,monkeypatch):
    pending=[];monkeypatch.setattr(song,'_launch',lambda *args:pending.append(args))
    state=post(client).json();path='/api/v1/transcriptions/'+state['job_id']
    assert client.post(path+'/cancel').json()['cancel_requested'] is True
    assert client.post(path+'/cancel').json()['status']=='running'
    song._run(*pending[0]);assert client.get(path).json()['status']=='cancelled'
    assert client.post(path+'/cancel').json()['status']=='cancelled'
    assert (song._folder(state['job_id'])/'original.audio').is_file()


def test_shared_slot_and_interruption(client,monkeypatch):
    pending=[];monkeypatch.setattr(song,'_launch',lambda *args:pending.append(args))
    first=post(client).json();assert post(client).status_code==409
    monkeypatch.setattr(trial,'_owner_token',lambda pid:None)
    assert client.get('/api/v1/transcriptions/'+first['job_id']).json()['stage']=='interrupted'
    os.close(pending[0][2])


def test_progress_and_cancel_cannot_become_success(client,monkeypatch):
    pending=[];monkeypatch.setattr(song,'_launch',lambda *args:pending.append(args))
    state=post(client).json();folder=song._folder(state['job_id']);(folder/'transcribe').mkdir()
    trial._write_json(folder/'transcribe/progress.json',dict(completed=1,total=3))
    value=client.get('/api/v1/transcriptions/'+state['job_id']).json()
    assert (value['completed_chunks'],value['total_chunks'])==(1,3)
    client.post('/api/v1/transcriptions/'+state['job_id']+'/cancel')
    with pytest.raises(trial.TrialFailure):song._state(folder,status='completed',stage='completed',midi_trial_id=str(uuid4()))
    os.close(pending[0][2])


def test_health_is_not_inference_verification(client):
    value=client.get('/api/v1/transcriptions/health').json()
    assert value['inference_verified'] is False and value['gpu_concurrency']==1
    assert value['max_audio_seconds']==600 and 'Redis' in value['legacy_basic_pitch']
    assert client.get('/api/v1/transcriptions/not-uuid').status_code==404


def test_cancel_terminates_real_owned_subprocess_and_releases_slot(client,tmp_path,monkeypatch):
    """Run an idle Python child, never a model, through the actual supervisor."""
    import subprocess
    import sys
    import threading
    import time
    fake=tmp_path/'idle-worker.py'
    fake.write_text("import os,time\nfrom pathlib import Path\nPath('child-pid').write_text(str(os.getpid()))\ntime.sleep(30)\n")
    monkeypatch.setattr(trial,'WORKER',fake)
    folder=tmp_path/'attempt';folder.mkdir();(folder/'workflow.json').write_text('{}')
    slot=trial._acquire_slot(trial._base());errors=[]
    def supervise():
        try:trial._run_stage('transcribe',SimpleNamespace(muscriptor_python=sys.executable),folder,folder/'input',slot)
        except trial.TrialFailure as exc:errors.append(exc.error.code)
    thread=threading.Thread(target=supervise);thread.start()
    deadline=time.monotonic()+5
    while not (folder/'child-pid').exists() and time.monotonic()<deadline:time.sleep(.02)
    assert (folder/'child-pid').exists()
    pid=int((folder/'child-pid').read_text());(folder/'cancel.requested').touch();thread.join(timeout=5)
    assert not thread.is_alive() and errors==['cancelled']
    summary=json.loads((folder/'transcribe-supervisor.json').read_text())
    assert summary['pid']==pid and summary['returncode'] is not None
    assert not Path(f'/proc/{pid}').exists()
    os.close(slot)
    next_slot=trial._acquire_slot(trial._base());os.close(next_slot)


@pytest.mark.parametrize('conflict',[False,True])
def test_fast_simultaneous_same_id_cannot_overwrite_completed_attempt(client,monkeypatch,conflict):
    stages(monkeypatch);identifier=str(uuid4());data=audio();acquire=trial._acquire_slot;first=True
    def raced(base):
        nonlocal first
        if first:
            first=False
            assert terminal(client,post(client,data,identifier))['status']=='completed'
        return acquire(base)
    monkeypatch.setattr(trial,'_acquire_slot',raced)
    assert post(client,audio(value=.1) if conflict else data,identifier).status_code==(409 if conflict else 202)
    assert client.get('/api/v1/transcriptions/'+identifier).json()['status']=='completed'


@pytest.mark.parametrize('name',['original.audio','selection.wav'])
def test_retry_rejects_modified_saved_inputs(client,monkeypatch,name):
    stages(monkeypatch,True);state=terminal(client,post(client));folder=song._folder(state['job_id'])
    (folder/name).write_bytes(audio(value=.1))
    response=client.post(f"/api/v1/transcriptions/{state['job_id']}/retry",data={'request_id':str(uuid4())})
    assert response.status_code==409 and response.json()['detail']['code']=='input_changed'


def test_cancel_cleans_owned_descendants_without_killing_unrelated_process(client,tmp_path,monkeypatch):
    import signal,subprocess,sys,threading,time
    fake=tmp_path/'tree-worker.py'
    fake.write_text("import os,subprocess,sys,time\nfrom pathlib import Path\nPath('parent-pid').write_text(str(os.getpid()))\nsubprocess.Popen([sys.executable,'-c',\"import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('descendant-pid').write_text(str(os.getpid()));time.sleep(30)\"])\ntime.sleep(30)\n")
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    folder=tmp_path/'tree-attempt';folder.mkdir();(folder/'workflow.json').write_text('{}')
    monkeypatch.setattr(trial,'WORKER',fake);slot=trial._acquire_slot(trial._base());errors=[]
    def supervise():
        try:trial._run_stage('transcribe',SimpleNamespace(muscriptor_python=sys.executable),folder,folder/'input',slot)
        except trial.TrialFailure as exc:errors.append(exc.error.code)
    thread=threading.Thread(target=supervise);thread.start();pgid=None
    try:
        deadline=time.monotonic()+5
        while not (folder/'descendant-pid').exists() and time.monotonic()<deadline:time.sleep(.02)
        assert (folder/'descendant-pid').exists()
        pgid=int((folder/'parent-pid').read_text());pid=int((folder/'descendant-pid').read_text())
        (folder/'cancel.requested').touch();thread.join(timeout=7)
        assert not thread.is_alive() and errors==['cancelled']
        stat=Path(f'/proc/{pid}/stat');deadline=time.monotonic()+2
        while stat.exists() and stat.read_text().rsplit(')',1)[1].split()[0]!='Z' and time.monotonic()<deadline:time.sleep(.02)
        assert not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z'
        assert unrelated.poll() is None
    finally:
        if pgid:
            try:os.killpg(pgid,signal.SIGKILL)
            except ProcessLookupError:pass
        thread.join(timeout=5);os.close(slot);unrelated.terminate();unrelated.wait(timeout=5)


@pytest.mark.parametrize('stage',['separate','transcribe'])
@pytest.mark.parametrize('product',[False,True])
def test_actual_worker_cli_argument_contract_without_model_inference(client,tmp_path,monkeypatch,stage,product):
    import sys
    from src.pipelines import muscriptor_worker
    wrapper=tmp_path/'worker-contract.py'
    wrapper.write_text(f"import sys,json\nfrom pathlib import Path\nsys.path.insert(0,{str(Path(muscriptor_worker.__file__).parent)!r})\nimport muscriptor_worker as worker\ndef run(stage,manifest,input_path,output,max_audio_seconds=10):\n output.mkdir()\n result=dict(status='completed',stage=stage,manifest=str(manifest),input=str(input_path),output=str(output),max_audio_seconds=max_audio_seconds)\n (output/'result.json').write_text(json.dumps(result))\n return result\nworker.run_stage=run\nraise SystemExit(worker.main())\n")
    folder=tmp_path/'contract';folder.mkdir();(folder/'deployment.json').write_text('{}')
    if product:(folder/'workflow.json').write_text('{}')
    monkeypatch.setattr(trial,'WORKER',wrapper);slot=trial._acquire_slot(trial._base())
    try:
        result=trial._run_stage(stage,SimpleNamespace(muscriptor_python=sys.executable,demucs_python=sys.executable),folder,folder/'selection.wav',slot)
        assert result['stage']==stage and result['max_audio_seconds']==(600 if product else 10)
        assert result['manifest']==str(folder/'deployment.json') and result['input']==str(folder/'selection.wav')
        assert result['output']==str(folder/stage)
    finally:os.close(slot)


@pytest.mark.parametrize('name',['original.audio','selection.wav','bass.wav','performance.mid'])
def test_download_rejects_changed_completed_artifact(client,monkeypatch,name):
    stages(monkeypatch);state=terminal(client,post(client));folder=song._folder(state['job_id'])
    (folder/song.ARTIFACTS[name]).write_bytes(b'changed')
    assert client.get(f"/api/v1/transcriptions/{state['job_id']}/files/{name}").status_code==409


def test_incomplete_same_id_and_corrupt_checkpoint_are_not_overwritten(client,monkeypatch):
    identifier=str(uuid4());folder=song._folder(identifier,exists=False);folder.mkdir()
    (folder/'status.json').write_text('retained-incomplete-state')
    assert post(client,identifier=identifier).status_code==409
    assert (folder/'status.json').read_text()=='retained-incomplete-state'
    stages(monkeypatch,True);state=terminal(client,post(client));old=song._folder(state['job_id'])
    (old/'separation-checkpoint.json').write_text('{')
    response=client.post(f"/api/v1/transcriptions/{state['job_id']}/retry",data={'request_id':str(uuid4())})
    assert response.status_code==409 and response.json()['detail']['code']=='invalid_checkpoint'


def test_launcher_cleans_descendant_after_leader_exit_only_owned_group(tmp_path):
    import runpy,signal,subprocess,sys,time
    marker=tmp_path/'helper-pid'
    child_code=f"import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)"
    leader_code=f"import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',{child_code!r}]);p=Path({str(marker)!r});exec('while not p.exists(): time.sleep(.01)')"
    leader=subprocess.Popen([sys.executable,'-c',leader_code],start_new_session=True)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    stop=runpy.run_path(str(Path(__file__).resolve().parents[3]/'scripts/run_local.py'))['stop_owned_group']
    try:
        leader.wait(timeout=5);assert leader.returncode==0
        stop(leader);pid=int(marker.read_text());stat=Path(f'/proc/{pid}/stat');deadline=time.monotonic()+2
        while stat.exists() and stat.read_text().rsplit(')',1)[1].split()[0]!='Z' and time.monotonic()<deadline:time.sleep(.02)
        assert not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z'
        assert unrelated.poll() is None
    finally:
        try:os.killpg(leader.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        unrelated.terminate();unrelated.wait(timeout=5)


def test_notation_rejection_keeps_verified_raw_midi_downloadable(client,monkeypatch):
    from fastapi import HTTPException
    stages(monkeypatch)
    def reject(*args,**kwargs):raise HTTPException(422,detail={'code':'duration_limit'})
    monkeypatch.setattr(song.midi_trials,'import_midi_bytes',reject)
    state=terminal(client,post(client));folder=song._folder(state['job_id'])
    assert state['status']=='failed' and state['error']['code']=='duration_limit'
    assert 'performance.mid' in state['available_files']
    response=client.get(f"/api/v1/transcriptions/{state['job_id']}/files/performance.mid")
    assert response.status_code==200 and response.content==(folder/'transcribe/performance.mid').read_bytes()


def test_interrupted_owner_cannot_retry_while_inherited_child_slot_is_alive(client,monkeypatch,tmp_path):
    import subprocess,sys
    pending=[];monkeypatch.setattr(song,'_launch',lambda *args:pending.append(args))
    state=post(client).json();slot=pending[0][2]
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],pass_fds=(slot,),start_new_session=True)
    os.close(slot)
    try:
        monkeypatch.setattr(trial,'_owner_token',lambda pid:None)
        response=client.get('/api/v1/transcriptions/'+state['job_id'])
        assert response.json()['stage']=='interrupted'
        retry=client.post(f"/api/v1/transcriptions/{state['job_id']}/retry",data={'request_id':str(uuid4())})
        assert retry.status_code==409 and retry.json()['detail']['code']=='trial_busy'
        assert client.get('/api/v1/transcriptions/'+state['job_id']).json()['status']=='failed'
    finally:trial._terminate(child)
