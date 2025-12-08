from fastapi.testclient import TestClient

from src.api.main import app
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def _touch_file(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_job_creation_smoke(monkeypatch, tmp_path) -> None:
    """
    Smoke test: enqueue a job and query its status with Celery eager execution.
    """
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks, "ensure_model", lambda model_name, cache_dir: cache_dir)
    monkeypatch.setattr(
        tasks,
        "separate_stems",
        lambda input_audio, output_dir, **kwargs: {
            stem: _touch_file(output_dir / f"{stem}.wav", b"wav")
            for stem in ("vocals", "drums", "bass", "other")
        },
    )
    monkeypatch.setattr(
        tasks,
        "transcribe_midi",
        lambda input_wav, output_dir, **kwargs: _touch_file(output_dir / "bass.mid", b"midi"),
    )
    monkeypatch.setattr(
        tasks,
        "midi_to_gp5",
        lambda midi_path, output_path, **kwargs: _touch_file(output_path, b"gp5"),
    )
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)

    client = TestClient(app)

    files = {"file": ("sample.wav", b"\x00\x01", "audio/wav")}
    response = client.post("/api/v1/jobs", files=files, data={"strings": 4})
    assert response.status_code == 202

    job = response.json()
    assert job["job_id"]

    status_response = client.get(f"/api/v1/jobs/{job['job_id']}")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["job_id"] == job["job_id"]
    assert payload["status"] in ("PENDING", "SUCCESS")
    assert "input.wav" in payload["files"]

