from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import main
from src.api.main import METADATA_FILENAME, app
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def _touch_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _setup_eager(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks, "ensure_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks,
        "separate_stems",
        lambda input_audio, output_dir, **kwargs: {
            stem: _touch_file(output_dir / f"{stem}.wav", b"x")
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


def test_create_and_get_job_success(monkeypatch, tmp_path) -> None:
    _setup_eager(monkeypatch, tmp_path)
    client = TestClient(app)

    files = {"file": ("tone.wav", b"\x00\x01", "audio/wav")}
    response = client.post("/api/v1/jobs", files=files, data={"strings": 4, "tuning": "standard"})

    assert response.status_code == 202
    job = response.json()
    assert job["job_id"]

    job_dir = settings.file_bucket_path / job["job_id"]
    assert (job_dir / "input.wav").exists()

    status_response = client.get(f"/api/v1/jobs/{job['job_id']}")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["job_id"] == job["job_id"]
    assert payload["status"] in ("PENDING", "SUCCESS")
    assert "input.wav" in payload["files"]
    if payload["status"] == "SUCCESS":
        assert payload["progress"] == 100


def test_rejects_unsupported_extension(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    client = TestClient(app)

    files = {"file": ("tone.txt", b"123", "text/plain")}
    response = client.post("/api/v1/jobs", files=files)

    assert response.status_code == 400
    assert "Unsupported file format" in response.json()["detail"]


def test_rejects_too_large(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)
    client = TestClient(app)

    oversized = io.BytesIO(b"x" * (main.MAX_UPLOAD_BYTES + 1))
    files = {"file": ("tone.wav", oversized, "audio/wav")}

    response = client.post("/api/v1/jobs", files=files)

    assert response.status_code == 413
    assert "File too large" in response.json()["detail"]


def test_missing_job_returns_404(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/jobs/non-existent")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_download_file_success(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    client = TestClient(app)

    job_id = "job-download"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    file_path = job_dir / "bass.wav"
    file_path.write_bytes(b"data")

    now = datetime.now(timezone.utc)
    metadata = main.JobStatusResponse(
        job_id=job_id,
        status=main.JobStatus.SUCCESS,
        progress=100,
        created_at=now,
        updated_at=now,
        files=["bass.wav"],
        error=None,
    )
    (job_dir / METADATA_FILENAME).write_text(metadata.model_dump_json(), encoding="utf-8")

    response = client.get(f"/api/v1/files/{job_id}", params={"name": "bass.wav"})
    assert response.status_code == 200
    assert response.content == b"data"
    assert response.headers["content-type"].startswith("audio/wav")


def test_download_file_not_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    client = TestClient(app)

    job_id = "missing-file"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    metadata = main.JobStatusResponse(
        job_id=job_id,
        status=main.JobStatus.SUCCESS,
        progress=100,
        created_at=now,
        updated_at=now,
        files=[],
        error=None,
    )
    (job_dir / METADATA_FILENAME).write_text(metadata.model_dump_json(), encoding="utf-8")

    response = client.get(f"/api/v1/files/{job_id}", params={"name": "bass.wav"})
    assert response.status_code == 404
    assert response.json()["detail"] == "File not found"

