"""Synthetic local derivatives, idempotency and artifact boundaries."""
import io
import uuid
from unittest.mock import patch

import mido
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.midi_trials import import_midi_bytes
from src.api import notation_trials as api
from src.core.config import settings
from src.pipelines.bass_output import BassOutputConfig
from tests.unit.test_evaluation_midi_clock import clock_source


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    midi=mido.MidiFile(ticks_per_beat=1000)
    track=mido.MidiTrack(); midi.tracks.append(track)
    track.extend([mido.Message("note_on",note=28,velocity=80,time=260),
                  mido.Message("note_off",note=28,time=700)])
    buffer=io.BytesIO(); midi.save(file=buffer)
    source=import_midi_bytes(buffer.getvalue(),filename="synthetic.mid",config=BassOutputConfig(profile="bass4"),source_kind="local-midi",preview_requested=False)
    request=dict(request_id=str(uuid.uuid4()),midi_trial_id=source.trial_id,input_sha256=source.input_sha256,
        config={"profile":"bass4","max_fret":24},fingering=True,max_position_shift=5,max_hand_span=4,
        rhythm=dict(tempo_bpm=120,numerator=4,denominator=4,origin_seconds=0,subdivisions_per_quarter=4,max_boundary_shift_ms=30),gp5="quarter-preview")
    return TestClient(app),request,tmp_path


def test_complete_retained_artifacts_and_idempotency(setup):
    client,request,folder=setup
    with patch.object(api,"run_bass_notation_trial",wraps=api.run_bass_notation_trial) as run:
        result=client.post("/api/v1/notation-trials",json=request)
        assert result.status_code==200
        body=result.json(); assert body["status"]=="completed"
        assert body["rhythm"]["events"][0]["start_change_ms"]==-5
        assert body["rhythm"]["events"][0]["end_change_ms"]==20
        assert client.post("/api/v1/notation-trials",json=request).json()==body
        assert run.call_count==1
    assert body["source_unchanged"] and body["input_note_count"]==1
    keys={a["key"] for a in body["artifacts"]}
    assert keys==set(api.FILES)-{"gp5-rhythm"}
    assert all(client.get(a["url"]).status_code==200 for a in body["artifacts"])
    assert "/mnt/" not in str(body) and "input_path" not in str(body)
    request["config"]["max_fret"]=20
    assert client.post("/api/v1/notation-trials",json=request).status_code==409


def test_bad_source_and_artifact_reject(setup):
    client,request,folder=setup
    request["input_sha256"]="0"*64
    assert client.post("/api/v1/notation-trials",json=request).status_code==409
    assert not (folder/"notation-trials"/request["request_id"]).exists()
    assert client.get("/api/v1/notation-trials/not-a-uuid").status_code==404


def test_unplayable_retains_original_and_rhythm(setup):
    client,request,folder=setup
    request["config"]={"profile":"custom","strings":1,"tuning_midi":[60],"max_fret":0}
    body=client.post("/api/v1/notation-trials",json=request).json()
    assert body["status"]=="blocked_optional_derivative"
    assert body["fingering"]["mapped_event_count"]==0
    assert body["gp5_status"]=="blocked"
    assert "gp5-preview" not in {a["key"] for a in body["artifacts"]}
    original=next(a for a in body["artifacts"] if a["key"]=="original-midi")
    path=folder/"notation-trials"/request["request_id"]/api.FILES["original-midi"]
    path.write_bytes(b"changed")
    assert client.get(original["url"]).status_code==409


def test_invalid_and_incomplete_choices(setup):
    client,request,_=setup
    request["rhythm"]["meter_changes"]=[{"bar":2,"numerator":3}]
    assert client.post("/api/v1/notation-trials",json=request).status_code==422
    del request["rhythm"]["meter_changes"]
    request["max_hand_span"]=None
    assert client.post("/api/v1/notation-trials",json=request).status_code==422


def test_exception_saved_as_terminal_and_never_repeated(setup):
    client,request,_=setup
    with patch.object(api,"run_bass_notation_trial",side_effect=ValueError("private path")) as run:
        body=client.post("/api/v1/notation-trials",json=request).json()
        assert body["status"]=="failed" and body["artifacts"]==[]
        assert "private path" not in str(body)
        assert client.post("/api/v1/notation-trials",json=request).json()==body
        assert run.call_count==1


def test_rhythmic_gp5_and_fractional_clock_fallback(setup):
    client,request,_=setup
    request["gp5"]="explicit-rhythm"
    body=client.post("/api/v1/notation-trials",json=request).json()
    assert body["status"]=="completed" and body["gp5"]["readback"]["event_readback_exact"]
    assert "gp5-rhythm" in {a["key"] for a in body["artifacts"]}
    request["request_id"]=str(uuid.uuid4());request["rhythm"]["tempo_bpm"]=120.5
    body=client.post("/api/v1/notation-trials",json=request).json()
    assert body["status"]=="blocked_optional_derivative" and body["gp5_status"]=="blocked"
    assert "rhythm-xml" in {a["key"] for a in body["artifacts"]}
    assert "gp5-rhythm" not in {a["key"] for a in body["artifacts"]}


def test_previously_stored_unsupported_clock_is_rejected_without_losing_source(setup):
    import hashlib,json
    client,request,folder=setup
    source_folder=folder/"midi-trials"/request["midi_trial_id"]
    data=clock_source("tempo",1)
    (source_folder/"input.mid").write_bytes(data)
    source=json.loads((source_folder/"summary.json").read_text())
    source["input_sha256"]=hashlib.sha256(data).hexdigest()
    (source_folder/"summary.json").write_text(json.dumps(source))
    request["input_sha256"]=source["input_sha256"]
    body=client.post("/api/v1/notation-trials",json=request).json()
    assert body["status"]=="failed" and body["error_code"]=="unsupported_midi_clock"
    assert "track0" in body["error"] and body["source_unchanged"]
    assert body["artifacts"]==[] and (source_folder/"input.mid").read_bytes()==data
    assert not (folder/"notation-trials"/request["request_id"]/"output").exists()
    assert client.post("/api/v1/notation-trials",json=request).json()==body


def test_source_changed_between_preflight_and_execution_is_not_misattributed(setup):
    client,request,folder=setup
    original=folder/"midi-trials"/request["midi_trial_id"]/"input.mid"
    before=original.read_bytes()
    raw=mido.MidiFile(file=io.BytesIO(before))
    for track in raw.tracks:
        for msg in track:
            if msg.type=="note_on":msg.velocity=95
    buffer=io.BytesIO();raw.save(file=buffer);changed=buffer.getvalue()
    real_run=api.run_bass_notation_trial
    def concurrent_edit(*args,**kwargs):
        original.write_bytes(changed)
        return real_run(*args,**kwargs)
    with patch.object(api,"run_bass_notation_trial",side_effect=concurrent_edit):
        body=client.post("/api/v1/notation-trials",json=request).json()
    assert body["status"]=="failed" and body["artifacts"]==[]
    assert original.read_bytes()==changed and changed!=before
    assert client.post("/api/v1/notation-trials",json=request).json()==body
