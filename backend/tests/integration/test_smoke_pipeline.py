from fastapi.testclient import TestClient

from src.api.main import app
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def test_job_creation_smoke(monkeypatch, tmp_path) -> None:
    """
    Smoke test: enqueue a job and query its status with Celery eager execution.
    """
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks, "ensure_model", lambda model_name, cache_dir: cache_dir)
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

