"""FastAPI routes for the CI/CD pipeline engine."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.pipeline import Pipeline, PipelineRun, PipelineStatus
from app.schemas.pipeline import PipelineCreate, PipelineOut, PipelineRunOut, ManualTrigger
from app.core.config import settings
from app.core.security import verify_github_signature
from app.websocket.manager import ws_manager

router = APIRouter(prefix="/api/v1", tags=["pipelines"])


# ── Pipelines CRUD ────────────────────────────────────────────────────────────

@router.post("/pipelines", response_model=PipelineOut, status_code=201)
def create_pipeline(payload: PipelineCreate, db: Session = Depends(get_db)):
    """Register a new project pipeline."""
    if db.query(Pipeline).filter_by(name=payload.name).first():
        raise HTTPException(409, f"Pipeline '{payload.name}' already exists")
    p = Pipeline(**payload.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    return p


@router.get("/pipelines", response_model=list[PipelineOut])
def list_pipelines(db: Session = Depends(get_db)):
    return db.query(Pipeline).filter_by(is_active=True).all()


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
def get_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return p


@router.delete("/pipelines/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    p.is_active = False; db.commit()


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook/github/{pipeline_id}", status_code=202)
async def github_webhook(
    pipeline_id: str,
    request    : Request,
    db         : Session = Depends(get_db),
):
    """
    GitHub push webhook endpoint.
    Validates HMAC-SHA256 signature, then triggers a pipeline run.
    Set this URL in your GitHub repo → Settings → Webhooks.
    """
    body = await verify_github_signature(request, settings.GITHUB_WEBHOOK_SECRET)

    pipeline = db.get(Pipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    payload  = json.loads(body)
    ref      = payload.get("ref", "")
    branch   = ref.replace("refs/heads/", "")

    # Only trigger on matching branch
    if branch != pipeline.branch:
        # 200, not 202: nothing was queued
        return JSONResponse(status_code=200, content={
            "message": f"Ignoring push to '{branch}' — watching '{pipeline.branch}'"
        })

    # Extract commit info
    head = payload.get("head_commit") or {}
    run  = PipelineRun(
        pipeline_id    = pipeline_id,
        trigger        = "webhook",
        branch         = branch,
        commit_sha     = payload.get("after", "")[:40],
        commit_message = (head.get("message") or "")[:500],
        pusher         = (payload.get("pusher") or {}).get("name", ""),
    )
    db.add(run); db.commit(); db.refresh(run)

    from app.tasks.pipeline_tasks import run_pipeline_task
    task = run_pipeline_task.apply_async(args=[run.id], queue="pipelines")
    run.celery_task_id = task.id; db.commit()

    return {"message": "Pipeline triggered", "run_id": run.id, "branch": branch}


# ── Manual trigger ────────────────────────────────────────────────────────────

@router.post("/pipelines/{pipeline_id}/trigger", response_model=PipelineRunOut, status_code=202)
def manual_trigger(
    pipeline_id: str,
    payload    : ManualTrigger,
    db         : Session = Depends(get_db),
):
    """Manually trigger a pipeline run — useful for testing."""
    pipeline = db.get(Pipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    # Check for already-running run
    active = db.query(PipelineRun).filter(
        PipelineRun.pipeline_id == pipeline_id,
        PipelineRun.status      == PipelineStatus.running,
    ).first()
    if active:
        raise HTTPException(409, f"Pipeline already running: run_id={active.id}")

    run = PipelineRun(
        pipeline_id = pipeline_id,
        trigger     = "manual",
        branch      = payload.branch,
        commit_sha  = "manual",
    )
    db.add(run); db.commit(); db.refresh(run)

    from app.tasks.pipeline_tasks import run_pipeline_task
    task = run_pipeline_task.apply_async(args=[run.id], queue="pipelines")
    run.celery_task_id = task.id; db.commit(); db.refresh(run)
    return run


# ── Run status ────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}", response_model=PipelineRunOut)
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/logs")
def get_run_logs(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit : int = Query(200, le=1000),
    db    : Session = Depends(get_db),
):
    """Fetch historical logs for a run — used on reconnect."""
    from app.models.pipeline import PipelineLog
    logs = (
        db.query(PipelineLog)
        .filter_by(run_id=run_id)
        .order_by(PipelineLog.line_number)
        .offset(offset).limit(limit)
        .all()
    )
    return [{"line_number": l.line_number, "content": l.content, "stream": l.stream} for l in logs]


@router.post("/runs/{run_id}/cancel", status_code=200)
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in (PipelineStatus.running, PipelineStatus.pending):
        raise HTTPException(409, f"Cannot cancel — status is '{run.status}'")
    if run.celery_task_id:
        from app.tasks.celery_app import celery_app
        celery_app.control.revoke(run.celery_task_id, terminate=True, signal="SIGTERM")
    run.status       = PipelineStatus.cancelled
    run.completed_at = datetime.utcnow()
    db.commit()
    return {"message": f"Run {run_id} cancelled"}


@router.get("/runs", response_model=list[PipelineRunOut])
def list_runs(
    pipeline_id: str | None = Query(None),
    status     : str | None = Query(None),
    limit      : int        = Query(20, le=100),
    offset     : int        = Query(0),
    db         : Session    = Depends(get_db),
):
    q = db.query(PipelineRun)
    if pipeline_id: q = q.filter(PipelineRun.pipeline_id == pipeline_id)
    if status:      q = q.filter(PipelineRun.status == status)
    return q.order_by(PipelineRun.started_at.desc()).offset(offset).limit(limit).all()


# ── WebSocket live log streaming ──────────────────────────────────────────────

@router.websocket("/ws/runs/{run_id}/logs")
async def websocket_logs(run_id: str, ws: WebSocket, db: Session = Depends(get_db)):
    """
    WebSocket endpoint for live log streaming.
    On connect: send historical logs, then stream new lines via Redis pub/sub.
    Client receives JSON: {"type": "log", "run_id": "...", "content": "...", "line_number": N}
    """
    await ws_manager.connect(run_id, ws)
    try:
        # Send historical logs so reconnect shows full history
        from app.models.pipeline import PipelineLog
        history = (
            db.query(PipelineLog)
            .filter_by(run_id=run_id)
            .order_by(PipelineLog.line_number)
            .all()
        )
        for entry in history:
            await ws.send_json({
                "type"       : "history",
                "run_id"     : run_id,
                "line_number": entry.line_number,
                "content"    : entry.content,
                "stream"     : entry.stream,
            })

        # Subscribe to Redis channel for new lines
        import redis.asyncio as aioredis
        r  = aioredis.from_url(settings.REDIS_URL)
        ps = r.pubsub()
        await ps.subscribe(f"pipeline_logs:{run_id}")

        async for message in ps.listen():
            if message["type"] == "message":
                await ws.send_text(message["data"].decode())

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(run_id, ws)
