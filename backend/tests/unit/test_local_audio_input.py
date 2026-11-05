"""Local AAC/ALAC/Ogg-Opus decode and pre-separation tests; no neural models."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from src.api import transcriptions as song, muscriptor_trials as trial
from src.api.main import app
from src.core.config import settings
from src.pipelines import local_audio_input as decoder

@pytest.fixture(scope='module')
def encoded(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Existing local ffmpeg/ffprobe required; never installed by tests')
    root = tmp_path_factory.mktemp('local-codecs')
    sr = 48000
    time = np.arange(round(2.025*sr))/sr
    wave = (.35*np.sin(2*np.pi*82.4069*time)+.1*np.sin(2*np.pi*164.8138*time)).astype(np.float32)
    source = root/'source.wav'
    sf.write(source, np.column_stack((wave, wave)), sr, subtype='FLOAT')
    output = {}
    for name, codec in [('aac.m4a', 'aac'), ('alac.m4a', 'alac'), ('test.opus', 'libopus'), ('vorbis.ogg', 'libvorbis')]:
        path = root/name
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-c:a', codec, str(path)], check=True, capture_output=True)
        output[name] = path.read_bytes()
    output['test.ogg'] = output['test.opus']
    return output


@pytest.mark.parametrize(('name','codec'), [('aac.m4a','aac'),('alac.m4a','alac'),('test.opus','opus'),('test.ogg','opus')])
def test_actual_decode_preserves_original_and_crop_is_decoded_sample_exact(encoded,name,codec):
    original = encoded[name]; before = hashlib.sha256(original).hexdigest()
    full, meta = decoder.decode_selection(original, Path(name).suffix, 0, None, max_seconds=600)
    cropped, selected = decoder.decode_selection(original, Path(name).suffix, .125, 1.625, max_seconds=600)
    all_samples, sr = sf.read(io.BytesIO(full), dtype='float32', always_2d=True)
    selected_samples, selected_sr = sf.read(io.BytesIO(cropped), dtype='float32', always_2d=True)
    assert sr == selected_sr == 48000
    assert np.array_equal(selected_samples, all_samples[6000:78000])
    assert selected['start_frame'] == 6000 and selected['end_frame'] == 78000
    assert selected['selected_seconds'] == 1.5 and selected['source_origin_seconds'] == .125
    assert meta['source_codec'] == codec and meta['decoder'] == 'ffmpeg-local'
    assert hashlib.sha256(original).hexdigest() == before


def test_ogg_is_a_container_and_vorbis_is_not_silently_accepted(encoded):
    with pytest.raises(decoder.AudioSelectionError, match='unsupported_codec'):
        decoder.decode_selection(encoded['vorbis.ogg'], '.ogg', 0, None, max_seconds=600)


def test_wrong_container_and_corrupt_input_fail_before_separation(encoded):
    for data, extension in [(encoded['test.opus'], '.m4a'), (b'not an Ogg stream', '.opus')]:
        with pytest.raises(decoder.AudioSelectionError, match='audio_decode_failed'):
            decoder.decode_selection(data, extension, 0, None, max_seconds=600)


def test_actual_pcm_length_limit_rejects_without_truncating_success(encoded):
    # Container metadata falls within the one-second padding allowance, but PCM exceeds 2s.
    with pytest.raises(decoder.AudioSelectionError, match='duration_limit'):
        decoder.decode_selection(encoded['test.opus'], '.opus', 0, None, max_seconds=2)


@pytest.mark.parametrize(('start','end'), [(-1,1),(0,float('nan')),(float('inf'),None),(1,1)])
def test_invalid_bounds_never_spawn_a_decoder(monkeypatch,start,end):
    monkeypatch.setattr(decoder.subprocess,'run',lambda *a,**k:pytest.fail('No process should start'))
    with pytest.raises(decoder.AudioSelectionError, match='invalid_selection'):
        decoder.decode_selection(b'x','.opus',start,end,max_seconds=600)


def test_missing_tools_size_and_busy_are_explicit_and_never_download(monkeypatch):
    monkeypatch.setattr(decoder.subprocess,'run',lambda *a,**k:pytest.fail('No process should start'))
    monkeypatch.setattr(decoder.shutil,'which',lambda name:None)
    with pytest.raises(decoder.AudioSelectionError,match='decoder_missing'):
        decoder.decode_selection(b'x','.m4a',0,None,max_seconds=600)
    monkeypatch.setattr(decoder,'MAX_UPLOAD_BYTES',0)
    with pytest.raises(decoder.AudioSelectionError,match='upload_limit'):
        decoder.decode_selection(b'x','.m4a',0,None,max_seconds=600)
    monkeypatch.setattr(decoder,'MAX_UPLOAD_BYTES',512*1024**2)
    monkeypatch.setattr(decoder.shutil,'which',lambda name:'/usr/bin/'+name)
    decoder._DECODER_SLOT.acquire()
    try:
        with pytest.raises(decoder.AudioSelectionError,match='decoder_busy'):
            decoder.decode_selection(b'x','.m4a',0,None,max_seconds=600)
    finally:
        decoder._DECODER_SLOT.release()


@pytest.mark.parametrize('name',['aac.m4a','alac.m4a','test.opus','test.ogg'])
def test_real_api_conversion_retention_download_and_same_id(encoded,tmp_path,monkeypatch,name):
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path)
    trial._SHUTDOWN.clear()
    monkeypatch.setattr(trial,'_load_deployment',lambda:SimpleNamespace(model_dump=lambda **kw:{'fixture':'pre-separation-only'}))
    launches=[]
    def before_separation(folder,deployment,slot,reuse):
        launches.append(folder)
        os.close(slot)
        song._state(folder,status='cancelled',stage='preprocessing-verified-no-model')
    monkeypatch.setattr(song,'_launch',before_separation)
    client=TestClient(app);identifier=str(uuid4());original=encoded[name]
    def post():
        return client.post('/api/v1/transcriptions',files={'file':(name,original)},
            data={'request_id':identifier,'rights_confirmed':'true'})
    rejected=client.post('/api/v1/transcriptions',files={'file':(name,original)},
        data={'request_id':identifier,'rights_confirmed':'true','end_seconds':'not-a-number'})
    assert rejected.status_code==422 and rejected.json()['detail'][0]['loc']==['body','end_seconds']
    assert not launches and not (song._base()/identifier).exists()
    response=post()
    assert response.status_code==202,response.text
    folder=launches[0]
    request=json.loads((folder/'request.json').read_text())
    assert request['selection']['source_extension']==Path(name).suffix
    assert (folder/'original.audio').read_bytes()==original
    assert sf.info(folder/'selection.wav').frames>0
    downloaded=client.get(f'/api/v1/transcriptions/{identifier}/files/original.audio')
    assert downloaded.status_code==200 and downloaded.content==original
    assert 'original'+Path(name).suffix in downloaded.headers['content-disposition']
    assert post().status_code==202 and len(launches)==1
    retained={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    conflict=client.post('/api/v1/transcriptions',files={'file':(name,original)},
        data={'request_id':identifier,'rights_confirmed':'true','start_seconds':'.125','end_seconds':'1.625'})
    assert conflict.status_code==409 and conflict.json()['detail']['code']=='request_conflict'
    assert len(launches)==1
    assert retained=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}


def test_decoder_commands_deny_network_and_external_movie_references(encoded,monkeypatch):
    actual=subprocess.run;calls=[]
    def record(command,**kwargs):
        calls.append(command)
        return actual(command,**kwargs)
    monkeypatch.setattr(decoder.subprocess,'run',record)
    decoder.decode_selection(encoded['aac.m4a'],'.m4a',0,None,max_seconds=600)
    for command in calls:
        assert command[command.index('-protocol_whitelist')+1]=='file'
        assert command[command.index('-enable_drefs')+1]=='0'


def test_local_storage_failure_is_explicit_without_inference(monkeypatch):
    monkeypatch.setattr(decoder.shutil,'which',lambda name:'/usr/bin/'+name)
    def unavailable(*args,**kwargs):
        raise OSError('Synthetic unavailable temporary storage')
    monkeypatch.setattr(decoder.tempfile,'TemporaryDirectory',unavailable)
    with pytest.raises(decoder.AudioSelectionError,match='decoder_runtime_unavailable'):
        decoder.decode_selection(b'x','.m4a',0,None,max_seconds=600)


@pytest.mark.parametrize('stage',['probe','decode'])
def test_decoder_failure_retains_stage_and_bounded_evidence_but_public_message_does_not_guess_cause(monkeypatch,stage):
    private=b'/tmp/private-path synthetic decoder error '+b'x'*10000
    monkeypatch.setattr(decoder.subprocess,'run',lambda *args,**kwargs:
                        subprocess.CompletedProcess(args[0],1,b'',private))
    with pytest.raises(decoder.AudioSelectionError) as caught:
        decoder._run(['/usr/bin/ffprobe' if stage=='probe' else '/usr/bin/ffmpeg'],15,stage=stage)
    error=caught.value
    assert error.code=='audio_decode_failed' and error.status==422
    assert error.failure is not None and error.failure.stage==stage
    assert error.failure.returncode==1 and len(error.failure.stderr)==8192
    assert 'ffprobe' in error.message if stage=='probe' else 'ffmpeg' in error.message
    assert '/tmp/private-path' not in error.message
    assert '破損' not in error.message and '保護' not in error.message


def test_api_decoder_evidence_remains_private(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path)
    monkeypatch.setattr(trial,'_load_deployment',lambda:SimpleNamespace(model_dump=lambda **kw:{}))
    def failure(*args,**kwargs):
        raise decoder.AudioSelectionError(422,'audio_decode_failed','音源情報の読み取り（ffprobe）に失敗しました。',
            failure=decoder.DecoderFailure('probe',1,'private stderr and source path'))
    monkeypatch.setattr(decoder,'decode_selection',failure)
    monkeypatch.setattr(song,'_launch',lambda *a,**k:pytest.fail('Rejected input must not launch'))
    identifier=str(uuid4())
    response=TestClient(app).post('/api/v1/transcriptions',files={'file':('fixture.m4a',b'synthetic')},
        data={'request_id':identifier,'rights_confirmed':'true'})
    assert response.status_code==422 and 'ffprobe' in response.text
    assert 'private stderr' not in response.text and not (song._base()/identifier).exists()


def test_private_local_failure_log_has_correlatable_id_but_no_paths_or_urls(monkeypatch):
    from structlog.testing import capture_logs
    command=['/usr/bin/ffprobe','-i','/tmp/stem2tab-audio-fixture/input.m4a']
    raw=b'/tmp/stem2tab-audio-fixture/input.m4a: Invalid data; https://user:secret@example.test/private'
    monkeypatch.setattr(decoder.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(command,1,b'',raw))
    with capture_logs() as logs,pytest.raises(decoder.AudioSelectionError) as caught:
        decoder._run(command,15,stage='probe')
    error=caught.value
    assert error.failure is not None
    assert error.failure.diagnostic_id in error.message
    assert logs[0]['diagnostic_id']==error.failure.diagnostic_id and logs[0]['stage']=='probe'
    assert logs[0]['stderr']=='<local-file>: Invalid data; <redacted-url>'
    assert 'Invalid data' not in error.message and 'secret' not in error.message


@pytest.mark.parametrize('extension',['wav','flac','m4a','opus','ogg'])
def test_empty_upload_rejected_before_model_checks_or_decoder(tmp_path,monkeypatch,extension):
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path)
    monkeypatch.setattr(trial,'_load_deployment',lambda:pytest.fail('Empty input must not check model runtimes'))
    monkeypatch.setattr(song,'_selection',lambda *a,**k:pytest.fail('Empty input must not decode'))
    monkeypatch.setattr(song,'_launch',lambda *a,**k:pytest.fail('Empty input must not launch'))
    identifier=str(uuid4());client=TestClient(app)
    for attempt in range(2):
        response=client.post('/api/v1/transcriptions',files={'file':('empty.'+extension,b'')},
            data={'request_id':identifier,'rights_confirmed':'true'})
        assert response.status_code==422
        assert response.json()['detail']==dict(code='empty_audio',message=decoder.EMPTY_AUDIO_MESSAGE)
        assert not (song._base()/identifier).exists()


def test_empty_decoder_input_never_starts_a_command(monkeypatch):
    monkeypatch.setattr(decoder.shutil,'which',lambda *a:pytest.fail('Empty input must not need a runtime'))
    with pytest.raises(decoder.AudioSelectionError,match='empty_audio') as caught:
        decoder.decode_selection(b'','.m4a',0,None,max_seconds=600)
    assert caught.value.failure is None and '0バイト' in caught.value.message
