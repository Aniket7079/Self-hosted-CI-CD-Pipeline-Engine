from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime


class PipelineCreate(BaseModel):
    name       : str = Field(..., min_length=3, max_length=200)
    repo_url   : str = Field(..., description="HTTPS or SSH URL of the Git repository")
    branch     : str = Field("main", description="Branch to watch for pushes")
    description: str | None = None


class PipelineOut(BaseModel):
    model_config = {"from_attributes": True}
    id         : str
    name       : str
    repo_url   : str
    branch     : str
    description: str | None
    is_active  : bool
    created_at : datetime


class LogLineOut(BaseModel):
    model_config = {"from_attributes": True}
    line_number: int
    stream     : str
    content    : str
    logged_at  : datetime


class PipelineRunOut(BaseModel):
    model_config = {"from_attributes": True}
    id            : str
    pipeline_id   : str
    celery_task_id: str | None
    status        : str
    trigger       : str
    commit_sha    : str | None
    commit_message: str | None
    pusher        : str | None
    branch        : str | None
    docker_image  : str | None
    started_at    : datetime
    completed_at  : datetime | None
    duration_sec  : int | None
    error_message : str | None
    logs          : list[LogLineOut] = []


class WebhookPayload(BaseModel):
    """GitHub push webhook — only fields we care about."""
    ref       : str = ""
    after     : str = ""
    repository: dict = {}
    pusher    : dict = {}
    head_commit: dict | None = None


class ManualTrigger(BaseModel):
    branch: str = "main"
