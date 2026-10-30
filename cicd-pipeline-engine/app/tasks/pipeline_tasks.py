"""
Celery pipeline task — orchestrates the full CI/CD flow:
  clone → build → test → push → deploy
Each step streams log lines to WebSocket clients via Redis pub/sub.
"""
import os
import asyncio
import logging
import tempfile
from datetime import datetime
from celery import Task

from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.pipeline import PipelineRun, PipelineLog, PipelineStatus
from app.core.config import settings

log = logging.getLogger(__name__)


class PipelineTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        run_id = args[0] if args else None
        if run_id:
            db = SessionLocal()
            try:
                run = db.get(PipelineRun, run_id)
                if run and run.status == PipelineStatus.running:
                    run.status        = PipelineStatus.failed
                    run.error_message = f"Worker crashed: {str(exc)[:500]}"
                    run.completed_at  = datetime.utcnow()
                    db.commit()
            finally:
                db.close()


def _save_log(db, run_id: str, line_num: int, content: str, stream: str = "stdout"):
    """Persist a log line and broadcast via Redis."""
    entry = PipelineLog(
        run_id      = run_id,
        line_number = line_num,
        content     = content,
        stream      = stream,
    )
    db.add(entry)
    db.commit()
    # Broadcast via Redis so WebSocket handler can push to clients
    import redis as redis_lib, json
    r = redis_lib.from_url(settings.REDIS_URL)
    r.publish(f"pipeline_logs:{run_id}", json.dumps({
        "type"       : "log",
        "run_id"     : run_id,
        "line_number": line_num,
        "content"    : content,
        "stream"     : stream,
    }))


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="app.tasks.pipeline_tasks.run_pipeline_task",
    max_retries=0,
    time_limit=7200,
    soft_time_limit=6900,
)
def run_pipeline_task(self, run_id: str) -> dict:
    """
    Full pipeline execution:
      1. Clone repository
      2. Build Docker image
      3. Run tests
      4. Push image (optional)
      5. SSH deploy (optional)
    """
    db       = SessionLocal()
    line_num = 0

    def emit(content: str, stream: str = "stdout"):
        nonlocal line_num
        line_num += 1
        log.info("[run:%s] %s", run_id[:8], content)
        _save_log(db, run_id, line_num, content, stream)

    try:
        run = db.get(PipelineRun, run_id)
        if not run:
            raise ValueError(f"PipelineRun {run_id} not found")

        run.status         = PipelineStatus.running
        run.celery_task_id = self.request.id
        db.commit()

        emit(f"🚀 Pipeline started — run_id: {run_id}")
        emit(f"📦 Repository: {run.pipeline.repo_url}")
        emit(f"🌿 Branch: {run.branch or run.pipeline.branch}")
        emit(f"👤 Triggered by: {run.pusher or 'manual'}")

        image_tag = f"cicd-{run.pipeline.name.lower().replace(' ','-')}:{run.commit_sha[:8] if run.commit_sha else 'latest'}"
        run.docker_image = image_tag
        db.commit()

        # ── Step 1: Clone ─────────────────────────────────────────
        emit("─" * 50)
        emit("STEP 1/4 — Cloning repository")
        with tempfile.TemporaryDirectory() as tmpdir:
            _clone_repo(run.pipeline.repo_url, run.branch or run.pipeline.branch, tmpdir, emit)

            from app.services.pipeline_config import load_pipeline_config
            pipeline_cfg = load_pipeline_config(tmpdir)
            run.steps_config = pipeline_cfg
            db.commit()
            emit(f"Loaded pipeline config (.cicd.yml): test_command='{pipeline_cfg['test_command']}'")

            # ── Step 2: Build ─────────────────────────────────────
            emit("─" * 50)
            emit("STEP 2/4 — Building Docker image")
            _build_image(tmpdir, image_tag, emit)

            # ── Step 3: Test ──────────────────────────────────────
            emit("─" * 50)
            emit("STEP 3/4 — Running tests")
            test_cmd = pipeline_cfg["test_command"]
            _run_tests(image_tag, test_cmd, emit)

        # ── Step 4: Deploy ────────────────────────────────────────
        emit("─" * 50)
        emit("STEP 4/4 — Deploying")
        if settings.DEPLOY_HOST and settings.DEPLOY_KEY_PATH:
            _ssh_deploy(run, image_tag, emit, pipeline_cfg)
        else:
            emit("[skip] No deploy credentials configured — skipping SSH deploy")
            emit("[info] Set DEPLOY_HOST and DEPLOY_KEY_PATH in .env to enable deployment")

        # ── Done ──────────────────────────────────────────────────
        emit("─" * 50)
        emit("✅ Pipeline SUCCEEDED")
        run.status       = PipelineStatus.success
        run.completed_at = datetime.utcnow()
        run.duration_sec = int((run.completed_at - run.started_at).total_seconds())
        db.commit()
        return {"status": "success", "run_id": run_id, "image": image_tag}

    except Exception as exc:
        emit(f"❌ Pipeline FAILED: {str(exc)}", stream="stderr")
        run = db.get(PipelineRun, run_id)
        if run:
            run.status        = PipelineStatus.failed
            run.error_message = str(exc)[:2000]
            run.completed_at  = datetime.utcnow()
            run.duration_sec  = int((run.completed_at - run.started_at).total_seconds())
            db.commit()
        raise
    finally:
        db.close()


def _clone_repo(repo_url: str, branch: str, dest: str, emit):
    import subprocess
    emit(f"$ git clone --depth 1 --branch {branch} {repo_url} .")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, repo_url, dest],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr[:500]}")
    emit("Git clone complete")


def _build_image(context_path: str, image_tag: str, emit):
    from app.services.docker_service import build_image
    for line in build_image(context_path, image_tag):
        emit(f"[build] {line}")
    emit(f"Image built: {image_tag}")


def _run_tests(image_tag: str, test_cmd: str, emit):
    from app.services.docker_service import run_tests_in_container
    emit(f"$ {test_cmd}")
    exit_code, lines = run_tests_in_container(image_tag, test_cmd)
    for line in lines:
        emit(f"[test]  {line}")
    if exit_code != 0:
        raise RuntimeError(f"Tests FAILED with exit code {exit_code}")
    emit("✅ All tests passed")


def _ssh_deploy(run, image_tag: str, emit, pipeline_cfg: dict):
    from app.services.ssh_service import SSHDeployer
    deployer = SSHDeployer(
        host    =settings.DEPLOY_HOST,
        user    =settings.DEPLOY_USER,
        key_path=settings.DEPLOY_KEY_PATH,
        port    =settings.DEPLOY_PORT,
    )
    deployer.connect()
    try:
        deploy_logs = deployer.deploy(
            image_tag      = image_tag,
            container_name = pipeline_cfg.get("container_name") or run.pipeline.name.lower().replace(" ", "-"),
            port_map       = pipeline_cfg.get("port_map", "8000:8000"),
        )
        for l in deploy_logs:
            emit(l)
        run.deploy_host = settings.DEPLOY_HOST
    finally:
        deployer.close()
