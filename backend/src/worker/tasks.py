from pathlib import Path

import structlog

from src.core.config import settings
from src.pipelines.demucs_loader import ensure_model
from src.worker.app import celery_app

logger = structlog.get_logger()


@celery_app.task
def process_job(job_id: str, payload: dict | None = None) -> dict[str, str]:
    """
    Stub task for processing a transcription job.

    Ensures the Demucs model is available and prepares an output directory.
    """
    cache_dir: Path = settings.demucs_cache_dir
    ensure_model(settings.demucs_model, cache_dir=cache_dir)

    output_dir = settings.file_bucket_path / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "job_processed_stub",
        job_id=job_id,
        cache_dir=str(cache_dir),
        output_dir=str(output_dir),
    )
    return {"job_id": job_id, "output_dir": str(output_dir)}

