from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.api.schemas import JobStatusResponse
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app


def _setup_eager(monkeypatch) -> None:
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    monkeypatch.setattr(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setattr(tasks.process_job, "update_state", lambda *args, **kwargs: None)


def test_process_job_pipeline(monkeypatch, tmp_path) -> None:
    _setup_eager(monkeypatch)
    monkeypatch.setattr(settings, "file_bucket_path", tmp_path)
    monkeypatch.setattr(tasks, "ensure_model", lambda *args, **kwargs: None)

    def fake_separate(input_audio: Path, output_dir: Path, **kwargs) -> dict[str, Path]:
        stems = {}
        for stem in ("vocals", "drums", "bass", "other"):
            dest = output_dir / f"{stem}.wav"
            dest.write_bytes(b"stem")
            stems[stem] = dest
        return stems

    def fake_transcribe(input_wav: Path, output_dir: Path, **kwargs) -> Path:
        midi_path = output_dir / "bass.mid"
        midi_path.write_bytes(b"midi")
        return midi_path

    def fake_tab(midi_path: Path, output_path: Path, **kwargs) -> Path:
        output_path.write_bytes(b"gp5")
        return output_path

    monkeypatch.setattr(tasks, "separate_stems", fake_separate)
    monkeypatch.setattr(tasks, "transcribe_midi", fake_transcribe)
    monkeypatch.setattr(tasks, "midi_to_gp5", fake_tab)

    job_id = "job-pipeline"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / "input.wav"
    input_path.write_bytes(b"audio")

    now = datetime.now(timezone.utc)
    metadata = JobStatusResponse(
        job_id=job_id,
        status=tasks.JobStatus.PENDING,
        progress=0,
        created_at=now,
        updated_at=now,
        files=["input.wav"],
        error=None,
    )
    tasks._write_metadata(metadata)

    payload = {"input_path": str(input_path), "strings": 4}
    result = tasks.process_job(job_id, payload)

    assert result["job_id"] == job_id

    meta = tasks._load_metadata(job_id)
    assert meta is not None
    assert meta.status == tasks.JobStatus.SUCCESS
    assert meta.progress == 100
    assert "bass.mid" in meta.files
    assert "bass.gp5" in meta.files
    assert "bass.wav" in meta.files

