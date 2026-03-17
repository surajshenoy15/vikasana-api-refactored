# app/workers/celery_app.py — Celery configuration for background tasks
import os
from celery import Celery

celery_app = Celery(
    "vikasana_workers",
    broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2"),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Auto-discover tasks from all feature modules
celery_app.autodiscover_tasks([
    "app.workers.tasks",
])
