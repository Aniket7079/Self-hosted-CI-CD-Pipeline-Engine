"""Runs before any app import: point the app at throwaway SQLite so the test
suite needs no external services (no PostgreSQL / Redis / Docker)."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_cicd_internal.db"
os.environ["DEBUG"] = "false"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
