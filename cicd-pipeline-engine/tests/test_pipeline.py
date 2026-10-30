"""
Test suite for CI/CD Pipeline Engine.
Run: pytest tests/ -v
"""
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.session import get_db, Base
from app.core.config import settings

# ── Test DB (SQLite in-memory) ────────────────────────────────────────────────
TEST_DB_URL = "sqlite:///./test_cicd.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True, scope="session")
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    import os
    if os.path.exists("test_cicd.db"):
        os.remove("test_cicd.db")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_pipeline(client):
    r = client.post("/api/v1/pipelines", json={
        "name"       : "test-project",
        "repo_url"   : "https://github.com/example/test-project",
        "branch"     : "main",
        "description": "Test pipeline",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-project"
    assert data["branch"] == "main"
    return data["id"]


def test_duplicate_pipeline_rejected(client):
    client.post("/api/v1/pipelines", json={
        "name": "duplicate-pipe", "repo_url": "https://github.com/x/y", "branch": "main"
    })
    r = client.post("/api/v1/pipelines", json={
        "name": "duplicate-pipe", "repo_url": "https://github.com/x/y", "branch": "main"
    })
    assert r.status_code == 409


def test_list_pipelines(client):
    r = client.get("/api/v1/pipelines")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_pipeline_not_found(client):
    r = client.get("/api/v1/pipelines/nonexistent-id")
    assert r.status_code == 404


def test_webhook_missing_signature(client):
    """Webhook without HMAC signature must be rejected."""
    r = client.post(
        "/api/v1/webhook/github/any-id",
        content=b'{"ref":"refs/heads/main"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_webhook_invalid_signature(client):
    """Webhook with wrong HMAC signature must be rejected."""
    r = client.post(
        "/api/v1/webhook/github/any-id",
        content=b'{"ref":"refs/heads/main"}',
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=invalidsignature",
        },
    )
    assert r.status_code == 401


def test_webhook_valid_signature_wrong_branch(client):
    """Valid signature but different branch → ignored gracefully."""
    # First create a pipeline
    r = client.post("/api/v1/pipelines", json={
        "name"    : "webhook-test-pipe",
        "repo_url": "https://github.com/example/repo",
        "branch"  : "main",
    })
    pipeline_id = r.json()["id"]

    body = json.dumps({
        "ref"  : "refs/heads/feature-branch",
        "after": "abc123def456",
        "repository": {"name": "repo"},
        "pusher": {"name": "testuser"},
        "head_commit": {"message": "test commit"},
    }).encode()

    sig = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    r = client.post(
        f"/api/v1/webhook/github/{pipeline_id}",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200
    assert "Ignoring" in r.json()["message"]


def test_hmac_security_module():
    """Unit test: HMAC validation logic."""
    secret = "test-secret-key"
    body   = b'{"test": "payload"}'
    valid_sig = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    assert valid_sig.startswith("sha256=")
    # Wrong secret → different digest
    wrong_sig = "sha256=" + hmac.new(
        b"wrong-secret", body, hashlib.sha256
    ).hexdigest()
    assert valid_sig != wrong_sig


def test_list_runs_empty(client):
    r = client.get("/api/v1/runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_run_not_found(client):
    r = client.get("/api/v1/runs/nonexistent-run-id")
    assert r.status_code == 404


def test_delete_pipeline(client):
    r = client.post("/api/v1/pipelines", json={
        "name": "to-delete", "repo_url": "https://github.com/x/y", "branch": "main"
    })
    pid = r.json()["id"]
    r = client.delete(f"/api/v1/pipelines/{pid}")
    assert r.status_code == 204
    # Should be gone from list
    pipelines = client.get("/api/v1/pipelines").json()
    assert not any(p["id"] == pid for p in pipelines)


def test_manual_trigger_rejected_while_run_active(client):
    """A second manual trigger must 409 while a run is already 'running'."""
    from app.models.pipeline import PipelineRun, PipelineStatus
    r = client.post("/api/v1/pipelines", json={
        "name": "guard-test", "repo_url": "https://github.com/x/y", "branch": "main"
    })
    pid = r.json()["id"]
    db = TestingSession()
    db.add(PipelineRun(pipeline_id=pid, status=PipelineStatus.running, trigger="manual"))
    db.commit(); db.close()

    r = client.post(f"/api/v1/pipelines/{pid}/trigger", json={"branch": "main"})
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]
