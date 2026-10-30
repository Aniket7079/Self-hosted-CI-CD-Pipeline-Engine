import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class PipelineStatus(str, enum.Enum):
    pending   = "pending"
    running   = "running"
    success   = "success"
    failed    = "failed"
    cancelled = "cancelled"


class Pipeline(Base):
    """Represents a registered project/repo with its pipeline config."""
    __tablename__ = "pipelines"

    id          : Mapped[str]      = mapped_column(String(36), primary_key=True, default=new_uuid)
    name        : Mapped[str]      = mapped_column(String(200), unique=True, nullable=False)
    repo_url    : Mapped[str]      = mapped_column(String(500), nullable=False)
    branch      : Mapped[str]      = mapped_column(String(100), default="main")
    description : Mapped[str|None] = mapped_column(Text)
    is_active   : Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at  : Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    runs: Mapped[list["PipelineRun"]] = relationship(
        "PipelineRun", back_populates="pipeline",
        order_by="PipelineRun.started_at.desc()"
    )


class PipelineRun(Base):
    """A single execution triggered by a push event or manual trigger."""
    __tablename__ = "pipeline_runs"

    id             : Mapped[str]      = mapped_column(String(36), primary_key=True, default=new_uuid)
    pipeline_id    : Mapped[str]      = mapped_column(String(36), ForeignKey("pipelines.id"))
    celery_task_id : Mapped[str|None] = mapped_column(String(155))
    status         : Mapped[str]      = mapped_column(SAEnum(PipelineStatus), default="pending")
    trigger        : Mapped[str]      = mapped_column(String(50), default="webhook")  # webhook | manual
    commit_sha     : Mapped[str|None] = mapped_column(String(40))
    commit_message : Mapped[str|None] = mapped_column(String(500))
    pusher         : Mapped[str|None] = mapped_column(String(100))
    branch         : Mapped[str|None] = mapped_column(String(100))
    docker_image   : Mapped[str|None] = mapped_column(String(300))
    deploy_host    : Mapped[str|None] = mapped_column(String(200))
    steps_config   : Mapped[dict|None]= mapped_column(JSON)   # parsed .cicd.yml steps
    started_at     : Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at   : Mapped[datetime|None] = mapped_column(DateTime)
    duration_sec   : Mapped[int|None] = mapped_column(Integer)
    error_message  : Mapped[str|None] = mapped_column(Text)

    pipeline: Mapped["Pipeline"] = relationship("Pipeline", back_populates="runs")
    logs    : Mapped[list["PipelineLog"]] = relationship(
        "PipelineLog", back_populates="run",
        order_by="PipelineLog.line_number", cascade="all, delete-orphan"
    )


class PipelineLog(Base):
    """Stores log lines for a pipeline run — streamed live via WebSocket."""
    __tablename__ = "pipeline_logs"

    id          : Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id      : Mapped[str]      = mapped_column(String(36), ForeignKey("pipeline_runs.id"))
    line_number : Mapped[int]      = mapped_column(Integer)
    stream      : Mapped[str]      = mapped_column(String(20), default="stdout")  # stdout|stderr|system
    content     : Mapped[str]      = mapped_column(Text)
    logged_at   : Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["PipelineRun"] = relationship("PipelineRun", back_populates="logs")
