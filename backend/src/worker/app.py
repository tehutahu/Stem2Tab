from celery import Celery

from src.core.config import settings

celery_app = Celery(
    "stem2tab",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
    include=["src.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
)

