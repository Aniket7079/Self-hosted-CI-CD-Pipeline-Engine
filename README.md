A small, self-hosted CI/CD runner: a GitHub push webhook triggers a Celery job that clones the repo, builds a Docker image, runs tests in the container, and (optionally) deploys over SSH — streaming every log line to a WebSocket and storing the full history in PostgreSQL.

Built with FastAPI · Celery · Redis · PostgreSQL · Docker SDK · Paramiko · WebSockets.
