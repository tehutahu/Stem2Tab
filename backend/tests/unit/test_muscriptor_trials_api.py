"""Synthetic bounded opt-in workflow checks; no models, devices or external calls."""
from __future__ import annotations

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

from src.api import muscriptor_trials as trial
from src.api.main import app
from src.core.config import settings
from src.worker import tasks


def audio(*, seconds=.2, channels=1, rate=8000, bad=False) -> bytes:
    values = np.zeros((int(seconds*rate),channels),dtype=np.float32)
    if bad:
        values[0,0]=np.nan
    stream=io.BytesIO()
    sf.write(stream,values,rate,format="WAV",subtype="FLOAT")
    return stream.getvalue()


def midi(path: Path, *, pitch=28) -> None:
    value=mido.MidiFile(type=1,ticks_per_beat=10000)
    value.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo",tempo=1000000,time=0),
        mido.Message("program_change",program=33,time=0),
        mido.Message("note_on",note=pitch,velocity=100,time=1000),
        mido.Message("note_off",note=pitch,velocity=0,time=2000),
    ]))
    value.save(path)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,"file_bucket_path",tmp_path)
    trial._SHUTDOWN.clear()
    monkeypatch.delenv("STEM2TAB_MUSCRIPTOR_DEPLOYMENT",raising=False)
    monkeypatch.setattr(trial,"WORKER",Path(__file__))
    def forbidden(*args,**kwargs):
        raise AssertionError("Default worker/model path must remain untouched")
    for name in ("ensure_model","separate_stems","transcribe_midi","midi_to_gp5"):
        monkeypatch.setattr(tasks,name,forbidden)
    monkeypatch.setattr(tasks.process_job,"apply_async",forbidden)
    return TestClient(app,raise_server_exceptions=False)


def ready(monkeypatch):
    deployment=SimpleNamespace(model_dump=lambda **kwargs:{"mock":"local assets"})
    monkeypatch.setattr(trial,"_load_deployment",lambda:deployment)
    return deployment


def post(client, data=None, filename="input.wav", **fields):
    return client.post("/api/v1/muscriptor-trials",files={"file":(filename,audio() if data is None else data,"audio/wav")},
        data={"request_id":str(uuid4()),"method":"muscriptor-small","rights_confirmed":"true","profile":"bass4",**fields})


def test_unconfigured_is_unavailable_and_does_not_start(client,tmp_path):
    response=client.get("/api/v1/muscriptor-trials/availability")
    assert response.status_code==200
    assert response.json()=={"available":False,"reasons":[{"code":"not_deployed",
        "message":"MuScriptor small local trial is not configured; no assets will be downloaded."}],
        "max_audio_seconds":10,"max_upload_bytes":10485760}
    assert post(client).status_code==503
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("fields",[{"method":""},{"method":"basic-pitch"},{"method":"muscriptor-large"},
    {"rights_confirmed":"false"},{"rights_confirmed":"1"}])
def test_explicit_opt_in_not_inferred(client,fields,tmp_path):
    response=post(client,**fields)
    assert response.status_code==422 and response.json()["detail"]["code"]=="explicit_opt_in_required"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("fields",[{"profile":""},{"profile":"custom"},{"profile":"bass4","strings":"5"},
    {"max_fret":"37"},{"gp5_preview":"maybe"}])
def test_invalid_bass_settings_rejected_before_execution(client,fields):
    assert post(client,**fields).status_code==422


@pytest.mark.parametrize("data,filename,status,code",[
    (b"not audio","input.wav",422,"invalid_audio"),
    (audio(seconds=10.000125),"input.wav",422,"duration_limit"),
    (audio(channels=3),"input.wav",422,"audio_format_limit"),
    (audio(rate=4000),"input.wav",422,"audio_format_limit"),
    (audio(bad=True),"input.wav",422,"nonfinite_audio"),
    (audio(),"input.mp3",415,"unsupported_audio"),
    (b"x"*(10*1024*1024+1),"input.wav",413,"upload_limit"),
])
def test_bounded_audio_no_silent_crop(client,monkeypatch,data,filename,status,code,tmp_path):
    ready(monkeypatch)
    response=post(client,data,filename)
    assert response.status_code==status and response.json()["detail"]["code"]==code
    assert not list(tmp_path.iterdir())


def stages(monkeypatch,*,missing=False,failure=None,pitch=28):
    seen=[]
    def stage(name,deployment,folder,input_path,slot):
        seen.append(name)
        output=folder/name
        output.mkdir()
        if name=="separate":
            if not missing:
                (output/"bass.wav").write_bytes(input_path.read_bytes())
            # Tempting other stem must never be substituted.
            (output/"other.wav").write_bytes(input_path.read_bytes())
        else:
            assert input_path.name=="bass.wav"
            if failure:
                raise trial.TrialFailure(failure,"Safe stage failure")
            midi(output/"performance.mid",pitch=pitch)
            (output/"events.jsonl").write_text('{"raw":true}\n')
        return {"status":"completed"}
    monkeypatch.setattr(trial,"_run_stage",stage)
    monkeypatch.setattr(trial,"_launch",trial._run_job)
    return seen


def test_complete_audio_to_strict_xml_retains_original_and_raw(client,monkeypatch,tmp_path):
    ready(monkeypatch)
    seen=stages(monkeypatch)
    original=audio()
    response=post(client,original,gp5_preview="true")
    assert response.status_code==202
    state=client.get(f"/api/v1/muscriptor-trials/{response.json()['job_id']}").json()
    assert state["status"]=="completed" and state["error"] is None
    assert seen==["separate","transcribe"]
    folder=tmp_path/"muscriptor-trials"/state["job_id"]
    assert (folder/"input.wav").read_bytes()==original
    assert (folder/"transcribe"/"events.jsonl").is_file()
    summary=client.get(f"/api/v1/midi-trials/{state['midi_trial_id']}").json()
    assert summary["input_note_count"]==1 and summary["source_kind"]=="muscriptor-small"
    assert summary["gp5"]["status"]=="written"
    xml=next(a for a in summary["artifacts"] if a["key"]=="storage-clock-xml")
    assert client.get(xml["url"]).status_code==200
    assert summary["storage_clock"]["musical_tempo_and_meter_verified"] is False
    assert str(tmp_path) not in json.dumps(state)
    assert json.loads((folder/"completion.json").read_text())["accuracy_evaluated"] is False
    # Previous job outputs stay unchanged and execution slot is released.
    second=post(client)
    assert second.status_code==202 and second.json()["job_id"]!=state["job_id"]
    assert (folder/"input.wav").read_bytes()==original


def test_missing_bass_is_terminal_no_fallback_no_transcription(client,monkeypatch):
    ready(monkeypatch)
    seen=stages(monkeypatch,missing=True)
    created=post(client).json()
    state=client.get(f"/api/v1/muscriptor-trials/{created['job_id']}").json()
    assert state["status"]=="failed" and state["error"]["code"]=="bass_missing"
    assert state["midi_trial_id"] is None and seen==["separate"]
    assert client.get(f"/api/v1/muscriptor-trials/{created['job_id']}").json()==state


def test_transcription_failure_terminal_and_releases_slot(client,monkeypatch):
    ready(monkeypatch)
    seen=stages(monkeypatch,failure="cuda_unavailable")
    created=post(client).json()
    state=client.get(f"/api/v1/muscriptor-trials/{created['job_id']}").json()
    assert state["status"]=="failed" and state["error"]["code"]=="cuda_unavailable"
    assert seen==["separate","transcribe"]
    assert post(client).status_code==202


def test_out_of_range_blocks_only_optional_gp5(client,monkeypatch):
    ready(monkeypatch)
    stages(monkeypatch,pitch=23)
    created=post(client,gp5_preview="true").json()
    state=client.get(f"/api/v1/muscriptor-trials/{created['job_id']}").json()
    assert state["status"]=="completed"
    summary=client.get(f"/api/v1/midi-trials/{state['midi_trial_id']}").json()
    assert summary["status"]=="gp5_blocked_out_of_range"
    assert summary["gp5"]["unmappable"][0]["midi"]==23
    assert any(a["key"]=="storage-clock-xml" for a in summary["artifacts"])


def test_concurrent_submit_rejected_without_duplicate_work(client,monkeypatch,tmp_path):
    ready(monkeypatch)
    launches=[]
    monkeypatch.setattr(trial,"_launch",lambda *args:launches.append(args))
    created=post(client)
    try:
        assert created.status_code==202
        second=post(client)
        assert second.status_code==409 and second.json()["detail"]["code"]=="trial_busy"
        assert len(launches)==1
        assert len([p for p in (tmp_path/"muscriptor-trials").iterdir() if p.is_dir()])==1
    finally:
        os.close(launches[0][-1])


def test_interrupted_owner_becomes_failed_without_retry(client,monkeypatch,tmp_path):
    ready(monkeypatch)
    launches=[]
    monkeypatch.setattr(trial,"_launch",lambda *args:launches.append(args))
    created=post(client).json()
    try:
        folder=tmp_path/"muscriptor-trials"/created["job_id"]
        request=json.loads((folder/"request.json").read_text())
        request["owner_token"]="prior-server-token"
        trial._write_json(folder/"request.json",request)
        state=client.get(f"/api/v1/muscriptor-trials/{created['job_id']}").json()
        assert state["status"]=="failed" and state["error"]["code"]=="owner_interrupted"
        assert len(launches)==1
    finally:
        os.close(launches[0][-1])


def test_unknown_job_safe_and_shutdown_prevents_start(client):
    assert client.get("/api/v1/muscriptor-trials/not-uuid").status_code==404
    assert client.get(f"/api/v1/muscriptor-trials/{uuid4()}").status_code==404
    trial._SHUTDOWN.set()
    try:
        assert post(client).status_code==503
    finally:
        trial._SHUTDOWN.clear()


def test_terms_are_not_accepted_by_api(client,monkeypatch,tmp_path):
    manifest=tmp_path/"deployment.json"
    manifest.write_text(json.dumps({"terms_accepted":False,"noncommercial":True}))
    monkeypatch.setenv("STEM2TAB_MUSCRIPTOR_DEPLOYMENT",str(manifest))
    response=client.get("/api/v1/muscriptor-trials/availability")
    assert response.json()["reasons"][0]["code"]=="terms_not_confirmed"
    assert json.loads(manifest.read_text())["terms_accepted"] is False


def test_duplicate_request_returns_same_result_without_reexecution(client,monkeypatch):
    ready(monkeypatch)
    seen=stages(monkeypatch)
    request_id=str(uuid4())
    original=audio()
    assert post(client,original,request_id=request_id).status_code==202
    repeat=post(client,original,request_id=request_id)
    assert repeat.status_code==202 and repeat.json()["status"]=="completed"
    assert repeat.json()["job_id"]==request_id
    assert seen==["separate","transcribe"]
    changed=post(client,audio(seconds=.3),request_id=request_id)
    assert changed.status_code==409 and changed.json()["detail"]["code"]=="request_conflict"
    assert seen==["separate","transcribe"]


@pytest.mark.parametrize("value",["", "../input", str(uuid4()).upper()])
def test_request_identifier_is_canonical(client,value):
    response=post(client,request_id=value)
    assert response.status_code==422 and response.json()["detail"]["code"]=="request_id_required"


@pytest.mark.parametrize("value",[None,[],"not-an-object"])
def test_invalid_manifest_shape_is_explicit(client,monkeypatch,tmp_path,value):
    manifest=tmp_path/"deployment.json"
    manifest.write_text(json.dumps(value))
    monkeypatch.setenv("STEM2TAB_MUSCRIPTOR_DEPLOYMENT",str(manifest))
    assert client.get("/api/v1/muscriptor-trials/availability").json()["reasons"][0]["code"]=="invalid_deployment"


def test_same_samples_with_different_wav_metadata_conflict_by_bytes(client, monkeypatch, tmp_path):
    """Same decoded waveform is not an identical request payload."""
    ready(monkeypatch)
    seen = stages(monkeypatch)
    request_id = str(uuid4())
    original = audio()
    metadata = b"JUNK" + (4).to_bytes(4, "little") + b"test"
    changed = original[:4] + (len(original) + len(metadata) - 8).to_bytes(4, "little") + original[8:] + metadata
    before, rate = sf.read(io.BytesIO(original))
    after, changed_rate = sf.read(io.BytesIO(changed))
    np.testing.assert_array_equal(before, after)
    assert rate == changed_rate and original != changed
    assert post(client, original, request_id=request_id).status_code == 202
    refused = post(client, changed, request_id=request_id)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "request_conflict"
    assert seen == ["separate", "transcribe"]
    assert (tmp_path / "muscriptor-trials" / request_id / "input.wav").read_bytes() == original


def test_invalid_model_output_keeps_raw_evidence_and_returns_no_score(client, monkeypatch, tmp_path):
    """Exercise API failure handling; inference itself remains a synthetic stub."""
    from src.pipelines.muscriptor_decoder import inspect_decoder_integrity
    ready(monkeypatch)
    seen = stages(monkeypatch)
    previous = trial._run_stage
    def stage(name, deployment, folder, input_path, slot):
        if name != "transcribe":
            return previous(name, deployment, folder, input_path, slot)
        seen.append(name)
        output = folder / name
        output.mkdir()
        notes = [{"onset": .3, "offset": .2, "pitch": 40}]
        (output / "notes.json").write_text(json.dumps(notes))
        (output / "tokens.jsonl").write_text('{"raw":true}\n')
        (output / "events.jsonl").write_text('{"raw":true}\n')
        integrity = inspect_decoder_integrity(notes, [])
        (output / "decoder-integrity.json").write_text(integrity.model_dump_json())
        raise trial.TrialFailure("invalid_model_output", "Raw output rejected; no note repair or retry.")
    monkeypatch.setattr(trial, "_run_stage", stage)
    created = post(client)
    assert created.status_code == 202
    state = client.get(f'/api/v1/muscriptor-trials/{created.json()["job_id"]}').json()
    assert state["status"] == "failed" and state["error"]["code"] == "invalid_model_output"
    assert state["midi_trial_id"] is None and seen == ["separate", "transcribe"]
    folder = tmp_path / "muscriptor-trials" / state["job_id"]
    assert json.loads((folder / "transcribe/notes.json").read_text())[0]["offset"] == .2
    assert json.loads((folder / "transcribe/decoder-integrity.json").read_text())["notes_modified"] is False
    assert (folder / "transcribe/tokens.jsonl").is_file() and (folder / "transcribe/events.jsonl").is_file()
    assert not (folder / "transcribe/performance.mid").exists()
    assert not (folder / "completion.json").exists()
    assert client.get(f'/api/v1/muscriptor-trials/{state["job_id"]}').json() == state
