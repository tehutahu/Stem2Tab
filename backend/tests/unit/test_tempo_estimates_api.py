"""Actual synthetic audio API and durable source correspondence, no models."""
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from src.api import tempo_estimates as api, midi_trials, transcriptions as song
from src.api.main import app
from src.core.config import settings
from src.evaluation.audio_tempo import TempoAudioError
from src.evaluation.audio_tempo_synthetic import pulse_audio
from tests.unit.test_evaluation_audio_tempo import native
from tests.unit.test_transcriptions_api import client, stages, terminal, post
from tests.unit.test_model_output_reviews import retained


def snapshot(folder):
    return {str(path.relative_to(folder)):path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def linked(client,monkeypatch,start=0):
    stages(monkeypatch)
    data=native(pulse_audio(bpm=120,seconds=24,rate=8000),8000)
    state=terminal(client,post(client,data,start_seconds=start,end_seconds=24))
    assert state["status"]=="completed",state
    return state,song._folder(state["job_id"])


def source(client,state):
    return client.get("/api/v1/tempo-estimates/sources",params=dict(midi_trial_id=state["midi_trial_id"],transcription_job_id=state["job_id"]))


def test_native_upload_analyzes_audio_only_and_preserves_manual_clock_path(client,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail("Tempo suggestions must never run source conversion, jobs, models or musical-clock export")
    monkeypatch.setattr(midi_trials,"import_midi_bytes",forbidden)
    data=native(pulse_audio(bpm=120,seconds=24),16000)
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("synthetic.wav",data)})
    assert response.status_code==200,response.text
    body=response.json()
    assert body["source"]["kind"]=="uploaded-audio" and body["source"]["correspondence"]=="unverified-upload"
    assert body["source"]["audio_sha256"]==hashlib.sha256(data).hexdigest() and body["source"]["audio_bytes"]==len(data)
    assert body["status"]=="candidates" and any(abs(c["bpm"]-120)<1 for c in body["candidates"])
    assert body["method"]["quality_calibration"]=="uncalibrated"
    assert not (settings.file_bucket_path/"midi-trials").exists()
    assert set(body)=={"schema_version","source","status","reason_codes","method","analyzed_intervals","coverage","candidates","windows","warnings"}


def test_completed_job_audio_reuses_exact_selection_without_writes(client,monkeypatch):
    state,folder=linked(client,monkeypatch,start=2)
    midi=midi_trials._trial_dir(state["midi_trial_id"])
    before,notes=snapshot(folder),snapshot(midi)
    verified=source(client,state)
    assert verified.status_code==200,verified.text
    descriptor=verified.json()
    assert descriptor["correspondence"]=="verified-completed-job"
    assert descriptor["source_origin_seconds"]==2 and descriptor["duration_seconds"]==22
    response=client.post("/api/v1/tempo-estimates/stored",json=descriptor)
    assert response.status_code==200,response.text
    result=response.json()
    assert result["source"]==descriptor and result["status"]=="candidates"
    assert result["analyzed_intervals"][0]["start_seconds"]==0
    assert before==snapshot(folder) and notes==snapshot(midi)


@pytest.mark.parametrize("what",["selection_bytes","original_bytes","request_origin","completion_selection","completion_midi","raw_midi","saved_midi","job_id"])
def test_completed_source_changes_are_rejected(client,monkeypatch,what):
    state,folder=linked(client,monkeypatch)
    verified=source(client,state);assert verified.status_code==200
    if what in {"selection_bytes","original_bytes","raw_midi","saved_midi"}:
        target={"selection_bytes":folder/"selection.wav","original_bytes":folder/"original.audio",
            "raw_midi":folder/"transcribe/performance.mid","saved_midi":midi_trials._trial_dir(state["midi_trial_id"])/"input.mid"}[what]
        target.write_bytes(target.read_bytes()+b"changed")
    else:
        target=folder/("request.json" if what=="request_origin" else "status.json" if what=="job_id" else "completion.json")
        record=json.loads(target.read_text())
        if what=="request_origin":record["selection"]["source_origin_seconds"]=1
        elif what=="completion_selection":record["selection"]["source_origin_seconds"]=1
        elif what=="completion_midi":record["raw_midi_sha256"]="f"*64
        else:record["job_id"]=str(uuid4())
        target.write_text(json.dumps(record))
    response=client.post("/api/v1/tempo-estimates/stored",json=verified.json())
    assert response.status_code==409,response.text
    assert "code" in response.json()["detail"]


def test_unrelated_job_cannot_supply_another_saved_midi_audio(client,monkeypatch):
    first,_=linked(client,monkeypatch)
    second,_=linked(client,monkeypatch)
    swapped={**first,"job_id":second["job_id"]}
    assert source(client,swapped).status_code==409
    descriptor=source(client,first).json()
    descriptor["transcription_job_id"]=second["job_id"]
    assert client.post("/api/v1/tempo-estimates/stored",json=descriptor).status_code==409


def test_descriptor_echo_is_checked_completely(client,monkeypatch):
    state,_=linked(client,monkeypatch)
    descriptor=source(client,state).json()
    for key,value in [("source_origin_seconds",1),("audio_sha256","f"*64),("duration_seconds",23),("sample_rate",16000),("midi_sha256","f"*64)]:
        changed={**descriptor,key:value}
        response=client.post("/api/v1/tempo-estimates/stored",json=changed)
        assert response.status_code==409,(key,response.text)
        assert response.json()["detail"]["code"]=="source_descriptor_changed"


def test_changed_receipts_during_cpu_work_do_not_release_candidates(client,monkeypatch):
    state,folder=linked(client,monkeypatch)
    descriptor=source(client,state).json()
    estimate=api.estimate_audio_tempo
    def mutate(stream,**kwargs):
        result=estimate(stream,**kwargs)
        target=folder/"completion.json";record=json.loads(target.read_text());record["midi_trial_id"]=str(uuid4());target.write_text(json.dumps(record))
        return result
    monkeypatch.setattr(api,"estimate_audio_tempo",mutate)
    assert client.post("/api/v1/tempo-estimates/stored",json=descriptor).status_code==409


def test_symlinked_audio_source_is_never_followed(client,monkeypatch,tmp_path):
    state,folder=linked(client,monkeypatch)
    target=tmp_path/"replacement.wav";target.write_bytes((folder/"selection.wav").read_bytes())
    (folder/"selection.wav").unlink();(folder/"selection.wav").symlink_to(target)
    assert source(client,state).status_code==409


def prepare_retained_audio(folder):
    data=native(pulse_audio(bpm=120,seconds=2,rate=8000),8000)
    (folder/"original.audio").write_bytes(data)
    (folder/"selection.wav").write_bytes(data)
    request=json.loads((folder/"request.json").read_text())
    request["selection"]=dict(source_duration_seconds=2,start_frame=0,end_frame=16000,sample_rate=8000,
        selected_seconds=2.,note_time_origin="selection_start",source_origin_seconds=0.,source_format="WAV")
    request.update(method="muscriptor-small",rights_confirmed=True,retry_of=None)
    original_settings=json.dumps(request,sort_keys=True).encode()
    request.update(original_sha256=hashlib.sha256(data).hexdigest(),selection_sha256=hashlib.sha256(data).hexdigest(),
        payload_sha256=hashlib.sha256(data+original_settings).hexdigest(),owner_pid=1,owner_token="synthetic")
    (folder/"request.json").write_text(json.dumps(request))


def test_review_preview_audio_requires_complete_source_plan_result_and_midi_receipts(retained):
    client,folder,_,_=retained
    prepare_retained_audio(folder)
    identifier=str(uuid4())
    result=client.post(f"/api/v1/transcriptions/{folder.name}/review-previews",data=dict(request_id=identifier,policy="exclude_zero_duration"))
    assert result.status_code==201 and result.json()["status"]=="completed",result.text
    state=dict(job_id=folder.name,midi_trial_id=identifier)
    before=snapshot(folder)
    verified=source(client,state)
    assert verified.status_code==200,verified.text
    assert verified.json()["correspondence"]=="verified-review-preview"
    estimated=client.post("/api/v1/tempo-estimates/stored",json=verified.json())
    assert estimated.status_code==200,estimated.text
    assert estimated.json()["status"]=="insufficient_evidence"
    assert before==snapshot(folder)
    # A changed origin with consistent frame length still cannot reuse this receipt.
    request=json.loads((folder/"request.json").read_text())
    request["selection"].update(start_frame=8000,end_frame=24000,source_origin_seconds=1.)
    (folder/"request.json").write_text(json.dumps(request))
    assert client.post("/api/v1/tempo-estimates/stored",json=verified.json()).status_code==409


@pytest.mark.parametrize("change",["preview_source_job","preview_hash","result_trial","missing_result"])
def test_review_preview_source_substitution_and_incomplete_receipts_refuse(retained,change):
    client,folder,_,_=retained;prepare_retained_audio(folder)
    identifier=str(uuid4())
    result=client.post(f"/api/v1/transcriptions/{folder.name}/review-previews",data=dict(request_id=identifier,policy="exclude_zero_duration"))
    assert result.json()["status"]=="completed"
    # Use the exact helper-owned preview folder rather than assume data-root layout.
    from src.api.model_output_reviews import _preview_folder
    path=_preview_folder(folder,identifier)
    if change=="missing_result":(path/"result.json").unlink()
    else:
        target=path/("request.json" if change in {"preview_source_job","preview_hash"} else "result.json")
        value=json.loads(target.read_text())
        if change=="preview_source_job":value["state"]["source_job_id"]=str(uuid4())
        elif change=="preview_hash":value["preview_sha256"]="f"*64
        else:value["midi_trial_id"]=str(uuid4())
        target.write_text(json.dumps(value))
    assert source(client,dict(job_id=folder.name,midi_trial_id=identifier)).status_code==409


@pytest.mark.parametrize("extension,data,expected",[(".mp3",b"not audio",415),(".wav",b"",422),(".wav",b"not WAV",422)])
def test_safe_upload_errors_and_no_unbounded_decoder(client,extension,data,expected):
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("synthetic"+extension,data)})
    assert response.status_code==expected,response.text
    assert set(response.json()["detail"])=={"code","message"}


def test_limits_before_decode_and_slot_release_after_every_failure(client,monkeypatch):
    monkeypatch.setattr(api,"MAX_UPLOAD_BYTES",20)
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",b"x"*21)})
    assert response.status_code==413
    with api._cpu_slot():
        response=client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",b"x")})
        assert response.status_code==409 and response.json()["detail"]["code"]=="tempo_cpu_busy"
    monkeypatch.setattr(api,"MAX_UPLOAD_BYTES",32*1024**2)
    data=native(pulse_audio(bpm=120,seconds=8),16000)
    estimate=api.estimate_audio_tempo
    def timeout(*args,**kwargs):raise TempoAudioError("analysis_budget","Synthetic cooperative budget",503)
    monkeypatch.setattr(api,"estimate_audio_tempo",timeout)
    assert client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",data)}).status_code==503
    monkeypatch.setattr(api,"estimate_audio_tempo",estimate)
    assert client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",data)}).status_code==200


def test_invalid_descriptor_and_id_validation_use_safe_error_envelope(client):
    response=client.get("/api/v1/tempo-estimates/sources",params=dict(midi_trial_id="../x",transcription_job_id="x"))
    assert response.status_code==422 and set(response.json()["detail"])=={"code","message"}
    assert client.post("/api/v1/tempo-estimates/stored",json={"path":"/private/audio.wav"}).status_code==422


def test_flac_expansion_duration_and_channel_rate_bounds_precede_analysis(client,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail("Invalid decoded metadata must not reach the estimator")
    monkeypatch.setattr(api,"estimate_audio_tempo",forbidden)
    cases=[native(np.zeros(121*8000,dtype=np.float32),8000,"FLAC"),
        native(np.zeros((1000,3),dtype=np.float32),8000),native(np.zeros(1000,dtype=np.float32),4000)]
    for data in cases:
        response=client.post("/api/v1/tempo-estimates/upload",files={"file":("synthetic.wav",data)})
        assert response.status_code==422,response.text


def test_content_format_is_checked_instead_of_trusting_filename(client):
    stream=io.BytesIO();sf.write(stream,np.zeros(8000),8000,format="OGG",subtype="VORBIS")
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("renamed.wav",stream.getvalue())})
    assert response.status_code==415 and response.json()["detail"]["code"]=="unsupported_audio"


def test_cpu_reservation_rejects_symlink_and_releases_on_unexpected_exception(client,tmp_path):
    base=settings.file_bucket_path/"tempo-estimates";base.mkdir()
    target=tmp_path/"other.lock";target.write_text("unchanged");(base/"cpu.lock").symlink_to(target)
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",b"invalid")})
    assert response.status_code==409 and target.read_text()=="unchanged"
    (base/"cpu.lock").unlink()
    with pytest.raises(RuntimeError):
        with api._cpu_slot():raise RuntimeError("synthetic failure")
    with api._cpu_slot():pass


def test_truncated_wav_refuses_instead_of_silently_shortening(client,monkeypatch):
    data=native(pulse_audio(bpm=120,seconds=24),16000)
    monkeypatch.setattr(api,"estimate_audio_tempo",lambda *args,**kwargs:pytest.fail("Truncated WAV must be refused before analysis"))
    response=client.post("/api/v1/tempo-estimates/upload",files={"file":("s.wav",data[:-32000])})
    assert response.status_code==422,response.text
    assert response.json()["detail"]["code"]=="invalid_audio"


@pytest.mark.parametrize("resolution",[1,2],ids=["before_analysis","final_revalidation"])
def test_saved_midi_disappearing_after_resolution_returns_safe_conflict(client,monkeypatch,resolution):
    state,_=linked(client,monkeypatch)
    descriptor=source(client,state).json()
    resolve=midi_trials._artifact_path
    estimate=api.estimate_audio_tempo
    resolutions=analyses=0
    def disappear(folder,key):
        nonlocal resolutions
        path=resolve(folder,key)
        if key=="original-midi":
            resolutions+=1
            if resolutions==resolution and path is not None:
                path.unlink()
        return path
    def actual_analysis(stream,**kwargs):
        nonlocal analyses
        analyses+=1
        return estimate(stream,**kwargs)
    monkeypatch.setattr(midi_trials,"_artifact_path",disappear)
    monkeypatch.setattr(api,"estimate_audio_tempo",actual_analysis)
    response=TestClient(app,raise_server_exceptions=False).post("/api/v1/tempo-estimates/stored",json=descriptor)
    assert resolutions==resolution and analyses==resolution-1
    assert response.status_code==409,response.text
    assert set(response.json())=={"detail"}
    assert response.json()["detail"]["code"]=="source_unavailable"
    with api._cpu_slot():pass
