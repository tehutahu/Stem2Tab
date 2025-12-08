from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from src.api.schemas import JobStatus, JobStatusResponse
from src.core.config import settings
from src.pipelines.demucs_loader import ensure_model
from src.pipelines.separation import separate_stems
from src.pipelines.tab import midi_to_gp5
from src.pipelines.transcription import transcribe_midi
from src.worker.app import celery_app

logger = structlog.get_logger()

METADATA_FILENAME = "metadata.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _job_dir(job_id: str) -> Path:
    return settings.file_bucket_path / job_id


def _metadata_path(job_id: str) -> Path:
    return _job_dir(job_id) / METADATA_FILENAME


def _list_job_files(job_id: str) -> list[str]:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        return []

    files: list[str] = []
    for path in job_dir.iterdir():
        if path.name == METADATA_FILENAME or path.is_dir():
            continue
        files.append(path.name)
    files.sort()
    return files


def _load_metadata(job_id: str) -> JobStatusResponse | None:
    meta_path = _metadata_path(job_id)
    if not meta_path.exists():
        return None
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return JobStatusResponse.model_validate(data)


def _write_metadata(payload: JobStatusResponse) -> None:
    meta_path = _metadata_path(payload.job_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(payload.model_dump_json(), encoding="utf-8")


def _update_metadata(
    job_id: str,
    *,
    status: JobStatus | None = None,
    progress: int | None = None,
    error: str | None = None,
    refresh_files: bool = False,
) -> JobStatusResponse:
    metadata = _load_metadata(job_id)
    if metadata is None:
        now = _now_utc()
        metadata = JobStatusResponse(
            job_id=job_id,
            status=status or JobStatus.PENDING,
            progress=progress or 0,
            created_at=now,
            updated_at=now,
            files=_list_job_files(job_id),
            error=None,
        )
    if status is not None:
        metadata.status = status
    if progress is not None:
        metadata.progress = progress
    if error is not None:
        metadata.error = error
    if refresh_files:
        metadata.files = _list_job_files(job_id)

    metadata.updated_at = _now_utc()
    _write_metadata(metadata)
    return metadata


def _set_basic_pitch_env() -> None:
    os.environ.setdefault("BASIC_PITCH_MODEL_SERIALIZATION", "onnx")


def _update_state(progress: int) -> None:
    try:
        process_job.update_state(state=JobStatus.STARTED.value, meta={"progress": progress})
    except Exception:  # pragma: no cover - defensive guard
        logger.warning("celery_update_state_failed", progress=progress)


@celery_app.task
def process_job(job_id: str, payload: dict | None = None) -> dict[str, str]:
    """
    Full processing pipeline: Demucs separation -> Basic Pitch -> GP5.
    """
    if payload is None:
        payload = {}

    cache_dir: Path = settings.demucs_cache_dir
    ensure_model(settings.demucs_model, cache_dir=cache_dir)
    _set_basic_pitch_env()

    input_path = Path(payload.get("input_path", ""))
    strings = int(payload.get("strings", 4))

    if not input_path.exists():
        raise FileNotFoundError(f"Input audio not found: {input_path}")

    output_dir = _job_dir(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "job_start",
        job_id=job_id,
        input_path=str(input_path),
        output_dir=str(output_dir),
        strings=strings,
        demucs_model=settings.demucs_model,
    )

    _update_metadata(job_id, status=JobStatus.STARTED, progress=5)
    _update_state(5)

    try:
        stems = separate_stems(
            input_audio=input_path,
            output_dir=output_dir,
            model_name=settings.demucs_model,
            cache_dir=cache_dir,
            job_id=job_id,
        )
        _update_metadata(job_id, progress=25, refresh_files=True)
        _update_state(25)

        bass_path = stems.get("bass") or next(iter(stems.values()))
        midi_path = transcribe_midi(bass_path, output_dir, job_id=job_id)
        _update_metadata(job_id, progress=55, refresh_files=True)
        _update_state(55)

        gp5_path = midi_to_gp5(midi_path, output_dir / "bass.gp5", strings=strings, job_id=job_id)
        if not gp5_path.exists():
            raise FileNotFoundError(f"GP5 not generated at {gp5_path}")
        _update_metadata(job_id, progress=80, refresh_files=True)
        _update_state(80)

        metadata = _update_metadata(job_id, status=JobStatus.SUCCESS, progress=100, refresh_files=True)
        _update_state(100)

        logger.info(
            "job_complete",
            job_id=job_id,
            files=metadata.files,
        )
        return {"job_id": job_id, "files": metadata.files}
    except Exception as exc:
        logger.exception("job_failed", job_id=job_id, error=str(exc))
        _update_metadata(job_id, status=JobStatus.FAILURE, error=str(exc), refresh_files=True)
        raise


