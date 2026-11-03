"""Synthetic retained-event review; no model/audio inference or private fixtures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import mido
import pytest

from src.api.main import app
from src.api import transcriptions as song, model_output_reviews as api, muscriptor_trials as trial
from src.core.config import settings
from src.pipelines import model_output_review as review, muscriptor_worker as worker
from src.pipelines.muscriptor_decoder import inspect_decoder_integrity


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,'file_bucket_path',tmp_path)
    monkeypatch.setattr(trial,'_load_deployment',lambda:pytest.fail('CPU review must not load models'))
    monkeypatch.setattr(trial,'_run_stage',lambda *a,**k:pytest.fail('CPU review must not start a worker'))
    identifier=str(uuid4());folder=song._folder(identifier,exists=False);output=folder/'transcribe'
    output.mkdir(parents=True)
    notes=[dict(index=0,pitch=40,program=33,instrument='electric_bass',is_drum=False,onset=.1,offset=.5),
           dict(index=1,pitch=40,program=33,instrument='electric_bass',is_drum=False,onset=.5,offset=.5),
           dict(index=2,pitch=40,program=33,instrument='electric_bass',is_drum=False,onset=.5,offset=1.)]
    tokens=[dict(type='chunk_boundary',seek_time=0.,next_seek_time=None),dict(type='token',event_type='tie',value=0),
            dict(type='token',event_type='shift',value=10),dict(type='token',event_type='shift',value=50),
            dict(type='token',event_type='shift',value=100)]
    trial._write_json(output/'notes.json',notes)
    (output/'tokens.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in tokens))
    (output/'events.jsonl').write_text('{"type":"synthetic_event_fixture"}\n')
    trial._write_json(output/'decoder-integrity.json',inspect_decoder_integrity(notes,tokens).model_dump(mode='json'))
    trial._write_json(output/'result.json',dict(status='failed',stage='transcribe',error=dict(code='invalid_model_output')))
    trial._write_json(folder/'request.json',dict(selection=dict(selected_seconds=2.),config=dict(profile='bass4',max_fret=24)))
    song._state(folder,status='failed',stage='invalid_model_output',error=trial.TrialError(code='invalid_model_output',message='Original failure'))
    receipt=api.review_base(folder)/'source';receipt.mkdir(parents=True)
    worker._literal_midi(receipt/'model-events.mid',notes,preserve_zero_duration=True)
    review.write_manifest(output,receipt/'model-events.mid',2.,'failed')
    before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    return TestClient(app),folder,notes,before


def test_raw_midi_preserves_zero_duration_and_every_event_pair(tmp_path):
    notes=[dict(index=i,program=33,pitch=40,onset=a,offset=b) for i,(a,b) in enumerate([(.1,.5),(.5,.5),(.5,1.)])]
    source=deepcopy(notes);path=tmp_path/'all-events.mid'
    result=worker._literal_midi(path,notes,preserve_zero_duration=True)
    midi=mido.MidiFile(path);clock=0;active={};rows=[]
    for event in midi:
        clock+=event.time
        if event.type=='note_on':
            assert event.note not in active;active[event.note]=clock
        elif event.type=='note_off':rows.append((event.note,active.pop(event.note),clock))
    assert len(rows)==3 and rows[1][1]==rows[1][2]
    assert rows[0][1:]==pytest.approx((.1,.5)) and rows[2][1:]==pytest.approx((.5,1.))
    assert result['zero_duration_events_preserved']==1 and notes==source
    with pytest.raises(worker.WorkerFailure):worker._literal_midi(tmp_path/'strict.mid',notes)


def test_legacy_status_projects_review_without_rewriting_the_job(retained):
    client,folder,notes,before=retained
    state=client.get('/api/v1/transcriptions/'+folder.name).json()
    assert state['status']=='needs_review' and state['review']['recorded_status']=='failed'
    assert state['review']['raw_note_count']==3 and state['review']['zero_duration_count']==1
    assert 'model-events.mid' in state['available_files'] and 'model-notes.json' in state['available_files']
    raw=client.get(f'/api/v1/transcriptions/{folder.name}/files/model-notes.json')
    assert raw.status_code==200 and raw.json()==notes
    downloaded=client.get(f'/api/v1/transcriptions/{folder.name}/files/model-events.mid')
    assert downloaded.status_code==200
    import io
    assert sum(e.type=='note_on' for e in mido.MidiFile(file=io.BytesIO(downloaded.content)))==3
    assert before=={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    assert json.loads((folder/'status.json').read_text())['status']=='failed'


def test_explicit_preview_is_cpu_only_idempotent_and_preserves_full_source(retained):
    client,folder,notes,before=retained;identifier=str(uuid4());url=f'/api/v1/transcriptions/{folder.name}/review-previews'
    assert client.post(url,data=dict(request_id=identifier)).status_code==422
    body=dict(request_id=identifier,policy='exclude_zero_duration')
    first=client.post(url,data=body)
    assert first.status_code==201,first.text
    value=first.json();assert value['status']=='completed' and value['midi_trial_id']==identifier
    assert (value['raw_note_count'],value['excluded_note_count'],value['retained_note_count'])==(3,1,2)
    assert client.post(url,data=body).json()==value
    assert client.get(url+'/'+identifier).json()==value
    summary=client.get('/api/v1/midi-trials/'+identifier).json()
    assert summary['input_note_count']==2 and 'ゼロ長1件を除外' in summary['warnings'][0]
    assert len(list((settings.file_bucket_path/'midi-trials').iterdir()))==1
    assert before=={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def test_changed_source_blocks_preview_and_raw_download_without_replacing_data(retained):
    client,folder,notes,before=retained
    (folder/'transcribe/notes.json').write_text('[]')
    url=f'/api/v1/transcriptions/{folder.name}'
    state=client.get(url).json();assert state['status']=='failed' and state['stage']=='review_source_changed'
    assert client.get(url+'/files/model-events.mid').status_code==409
    assert client.post(url+'/review-previews',data=dict(request_id=str(uuid4()),policy='exclude_zero_duration')).status_code==409
    assert not (settings.file_bucket_path/'midi-trials').exists()
    assert (folder/'transcribe/notes.json').read_text()=='[]'


def test_recovery_after_response_record_loss_does_not_convert_again(retained,monkeypatch):
    client,folder,_,_=retained;identifier=str(uuid4());url=f'/api/v1/transcriptions/{folder.name}/review-previews'
    result=client.post(url,data=dict(request_id=identifier,policy='exclude_zero_duration')).json()
    path=api._preview_folder(folder,identifier);(path/'result.json').unlink()
    monkeypatch.setattr(api.midi_trials,'import_midi_bytes',lambda *a,**k:pytest.fail('Recovery must not convert again'))
    assert client.get(url+'/'+identifier).json()==result


def test_recovery_cannot_complete_from_changed_source(retained):
    client,folder,_,_=retained;identifier=str(uuid4());url=f'/api/v1/transcriptions/{folder.name}/review-previews'
    assert client.post(url,data=dict(request_id=identifier,policy='exclude_zero_duration')).status_code==201
    path=api._preview_folder(folder,identifier);(path/'result.json').unlink()
    (folder/'transcribe/notes.json').write_text('[]')
    assert client.get(url+'/'+identifier).status_code==409
    assert not (path/'result.json').exists()


def test_status_recovery_does_not_race_an_in_progress_conversion(retained):
    client,folder,_,_=retained;identifier=str(uuid4());url=f'/api/v1/transcriptions/{folder.name}/review-previews'
    assert client.post(url,data=dict(request_id=identifier,policy='exclude_zero_duration')).status_code==201
    with api._lock(folder,identifier):
        response=client.get(url+'/'+identifier)
        assert response.status_code==409 and response.json()['detail']['code']=='review_preview_busy'
    assert client.get(url+'/'+identifier).json()['status']=='completed'


def test_nonzero_timing_error_and_regression_are_not_reviewed(retained):
    _,folder,notes,_=retained;output=folder/'transcribe'
    notes[1]['offset']=.4
    trial._write_json(output/'notes.json',notes)
    tokens=[];trial._write_json(output/'decoder-integrity.json',inspect_decoder_integrity(notes,tokens).model_dump(mode='json'))
    with pytest.raises(review.ReviewDataError):review.inspect_source(output,2.,'failed')


def test_worker_receipt_requires_verified_assets(retained):
    client,folder,notes,_=retained;output=folder/'transcribe'
    worker._literal_midi(output/'model-events.mid',notes,preserve_zero_duration=True)
    review.write_manifest(output,output/'model-events.mid',2.,'needs_review')
    result=dict(status='needs_review',assets_verified_after=True,
        output_review_manifest_sha256=review.digest(output/'output-review.json'))
    trial._write_json(output/'result.json',result)
    song._state(folder,status='needs_review',stage='output_needs_review')
    url='/api/v1/transcriptions/'+folder.name
    assert client.get(url).json()['review']['recorded_status']=='needs_review'
    result['assets_verified_after']=False;trial._write_json(output/'result.json',result)
    assert client.get(url).json()['stage']=='review_source_changed'
    assert client.post(url+'/review-previews',data=dict(request_id=str(uuid4()),policy='exclude_zero_duration')).status_code==409


def test_preview_uuid_cannot_be_reused_across_source_jobs(retained):
    client,folder,_,_=retained;identifier=str(uuid4())
    url='/api/v1/transcriptions/'+folder.name+'/review-previews'
    assert client.post(url,data=dict(request_id=identifier,policy='exclude_zero_duration')).status_code==201
    other=song._folder(str(uuid4()),exists=False);other.mkdir()
    assert client.get('/api/v1/transcriptions/'+other.name+'/review-previews/'+identifier).status_code==409


def test_token_regression_is_not_zero_only_review(retained):
    _,folder,notes,_=retained;output=folder/'transcribe'
    tokens=[dict(type='chunk_boundary',seek_time=0.,next_seek_time=None),
        dict(type='token',event_type='tie',value=0),dict(type='token',event_type='shift',value=50),
        dict(type='token',event_type='shift',value=10)]
    (output/'tokens.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in tokens))
    trial._write_json(output/'decoder-integrity.json',inspect_decoder_integrity(notes,tokens).model_dump(mode='json'))
    with pytest.raises(review.ReviewDataError):review.inspect_source(output,2.,'failed')
