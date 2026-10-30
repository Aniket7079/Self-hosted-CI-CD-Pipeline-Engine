from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "cicd_engine",
    broker =settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.pipeline_tasks"],
)

celery_app.conf.update(
    task_serializer   = "json",
    accept_content    = ["json"],
    result_serializer = "json",
    timezone          = "UTC",
    enable_utc        = True,
    task_track_started= True,
    task_acks_late    = True,
    worker_prefetch_multiplier = 1,
    task_routes = {
        "app.tasks.pipeline_tasks.run_pipeline_task": {"queue": "pipelines"},
    },
)
