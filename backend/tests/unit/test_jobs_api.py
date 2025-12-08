from __future__ import annotations

import io

from fastapi.testclient import TestClient

from src.api import main
from src.api.main import app
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def _setup_eager(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks, "ensure_model", lambda *args, **kwargs: None)
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

