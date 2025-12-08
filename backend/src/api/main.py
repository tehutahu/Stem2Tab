from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog
from celery.result import AsyncResult
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status

from src.api.schemas import JobCreateResponse, JobStatus, JobStatusResponse
from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app

logger = structlog.get_logger()

ALLOWED_EXTENSIONS = {"mp3", "wav", "m4a", "ogg", "flac"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
METADATA_FILENAME = "metadata.json"


app = FastAPI(title="Stem2Tab API")


@app.get("/health")
def health() -> dict[str, str]:
    """Health endpoint for liveness probes."""
    return {"status": "ok"}


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


def _validate_upload(file: UploadFile) -> str:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is required")

    ext = Path(file.filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format. Allowed: {allowed}",
        )

    file.file.seek(0, 2)
    size_bytes = file.file.tell()
    file.file.seek(0)
    if size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File too large. Max 50MB.",
        )

    return ext


def _save_upload(job_id: str, upload: UploadFile, ext: str) -> Path:
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    destination = job_dir / f"input.{ext}"
    with destination.open("wb") as fp:
        shutil.copyfileobj(upload.file, fp)
    return destination


def _write_metadata(payload: JobStatusResponse) -> None:
    meta_path = _metadata_path(payload.job_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(payload.model_dump_json(), encoding="utf-8")


def _load_metadata(job_id: str) -> JobStatusResponse:
    meta_path = _metadata_path(job_id)
    if not meta_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    data = json.loads(meta_path.read_text(encoding="utf-8"))
    return JobStatusResponse.model_validate(data)


def _refresh_status(job_id: str, metadata: JobStatusResponse) -> JobStatusResponse:
    async_result = AsyncResult(job_id, app=celery_app)
    if async_result.status in JobStatus.__members__:
        metadata.status = JobStatus(async_result.status)
    else:
        metadata.status = JobStatus.PENDING

    if async_result.failed():
        metadata.error = str(async_result.result)
    elif async_result.successful():
        metadata.progress = 100

    metadata.files = _list_job_files(job_id)
    metadata.updated_at = _now_utc()
    _write_metadata(metadata)
    return metadata


@app.post(
    "/api/v1/jobs",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    file: UploadFile = File(...),
    strings: int = Form(4),
    tuning: str = Form("standard"),
) -> JobCreateResponse:
    """Create a job, persist the upload, and enqueue processing."""
    job_id = str(uuid4())
    ext = _validate_upload(file)

    input_path = _save_upload(job_id, file, ext)
    created_at = _now_utc()
    metadata = JobStatusResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        progress=0,
        created_at=created_at,
        updated_at=created_at,
        files=_list_job_files(job_id),
        error=None,
    )
    _write_metadata(metadata)

    payload = {
        "job_id": job_id,
        "input_path": str(input_path),
        "strings": strings,
        "tuning": tuning,
        "original_filename": file.filename,
    }

    try:
        async_result = tasks.process_job.apply_async(kwargs={"job_id": job_id, "payload": payload}, task_id=job_id)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("job_enqueue_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to enqueue job") from exc

    logger.info(
        "job_enqueued",
        job_id=job_id,
        demucs_model=settings.demucs_model,
        input_path=str(input_path),
        strings=strings,
        tuning=tuning,
    )
    return JobCreateResponse(job_id=async_result.id)


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    """Retrieve the current status for a job."""
    metadata = _load_metadata(job_id)
    return _refresh_status(job_id, metadata)

