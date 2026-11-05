"""Synthetic normal job -> preserved MIDI -> optional rhythmic GP5 HTTP flow."""
import hashlib
from uuid import uuid4
from fastapi.testclient import TestClient
import pretty_midi
from src.api.main import app
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def test_normal_job_midi_enters_preserved_and_explicit_workflows(monkeypatch, tmp_path):
    """Mock inference only; exercise actual saved-MIDI and derivative exports."""
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks, "ensure_model", lambda *args, **kwargs: None)

    def separate(input_audio, output_dir, **kwargs):
        path = output_dir / "bass.wav"
        path.write_bytes(b"synthetic separation placeholder")
        return {"bass": path}

    def transcribe(input_wav, output_dir, **kwargs):
        midi = pretty_midi.PrettyMIDI(initial_tempo=120, resolution=960)
        bass = pretty_midi.Instrument(33)
        bass.notes = [pretty_midi.Note(80, 28, .130, .480), pretty_midi.Note(80, 33, .5, .75)]
        midi.instruments.append(bass)
        path = output_dir / "bass.mid"
        midi.write(str(path))
        return path

    def legacy_preview(midi_path, output_path, **kwargs):
        output_path.write_bytes(b"legacy preview placeholder")
        return output_path

    monkeypatch.setattr(tasks, "separate_stems", separate)
    monkeypatch.setattr(tasks, "transcribe_midi", transcribe)
    monkeypatch.setattr(tasks, "midi_to_gp5", legacy_preview)
    client = TestClient(app)
    created = client.post("/api/v1/jobs", files={"file": ("synthetic.wav", b"synthetic", "audio/wav")})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "SUCCESS"
    raw = client.get(f"/api/v1/files/{job_id}", params={"name": "bass.mid"}).content
    digest = hashlib.sha256(raw).hexdigest()
    imported = client.post("/api/v1/midi-trials", files={"file": ("bass.mid", raw, "audio/midi")},
                           data={"profile": "bass4", "source_kind": "local-midi", "max_fret": "24", "gp5_preview": "false"})
    assert imported.status_code == 201
    source = imported.json()
    assert source["input_sha256"] == digest and source["input_note_count"] == 2
    assert source["gp5"]["status"] == "not_requested"
    original_url = next(a["url"] for a in source["artifacts"] if a["key"] == "original-midi")
    assert client.get(original_url).content == raw
    response = client.post("/api/v1/notation-trials", json={
        "request_id": str(uuid4()), "midi_trial_id": source["trial_id"], "input_sha256": digest,
        "config": {"profile": "bass4", "max_fret": 24}, "fingering": True,
        "max_position_shift": 5, "max_hand_span": 4,
        "rhythm": {"tempo_bpm": 120, "numerator": 4, "denominator": 4, "origin_seconds": 0,
                   "subdivisions_per_quarter": 8, "max_boundary_shift_ms": 30}, "gp5": "explicit-rhythm"})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed" and result["source_unchanged"]
    assert result["gp5_status"] == "written" and result["gp5"]["readback"]["logical_note_count"] == 2
    assert result["rhythm"]["changed_event_count"] == 1
    assert client.get(original_url).content == raw
    gp5_url = next(a["url"] for a in result["artifacts"] if a["key"] == "gp5-rhythm")
    assert client.get(gp5_url).content.startswith(b"\x18FICHIER GUITAR PRO")
