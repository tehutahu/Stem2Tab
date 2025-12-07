from uuid import uuid4

import structlog
from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.core.config import settings
from src.worker import tasks
from src.worker.app import celery_app

logger = structlog.get_logger()


class JobCreate(BaseModel):
    """Payload for creating a transcription job."""

    filename: str | None = None


class JobStatus(BaseModel):
    """Represents the current state of a job."""

    job_id: str
    status: str
    result: dict | None = None
    error: str | None = None


app = FastAPI(title="Stem2Tab API")


@app.get("/health")
def health() -> dict[str, str]:
    """Health endpoint for liveness probes."""
    return {"status": "ok"}


def _get_status(job_id: str) -> JobStatus:
    async_result = AsyncResult(job_id, app=celery_app)
    payload = JobStatus(job_id=job_id, status=async_result.status)

    if async_result.failed():
        payload.error = str(async_result.result)
    elif async_result.successful():
        payload.result = async_result.result

    return payload


@app.post("/api/v1/jobs", response_model=JobStatus)
def create_job(job: JobCreate) -> JobStatus:
    """Create a job and enqueue it for processing."""
    job_id = str(uuid4())
    try:
        async_result = tasks.process_job.delay(job_id=job_id, payload=job.model_dump())
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("job_enqueue_failed", job_id=job_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to enqueue job") from exc

    logger.info("job_enqueued", job_id=job_id, demucs_model=settings.demucs_model)
    return _get_status(async_result.id)


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    """Retrieve the current status for a job."""
    return _get_status(job_id)

