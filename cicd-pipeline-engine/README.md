# 🚀 CI/CD Pipeline Engine

A small, self-hosted CI/CD runner: a GitHub push webhook triggers a Celery job that **clones the repo, builds a
Docker image, runs tests in the container, and (optionally) deploys over SSH** — streaming every log line to a
WebSocket and storing the full history in PostgreSQL.

Built with FastAPI · Celery · Redis · PostgreSQL · Docker SDK · Paramiko · WebSockets.

---

## ✨ What it does

| Feature | Status |
|---|---|
| 🔐 GitHub webhook with **HMAC-SHA256** verification (constant-time compare) | ✅ verified live (bad sig → `401`, good sig → `202`) |
| ⚙️ Async execution via Celery (API returns immediately) | ✅ verified live |
| 📂 Clone repo → read `.cicd.yml` → fail fast on a bad config | ✅ verified live against a real git repo |
| 📡 WebSocket log streaming, **history replay on reconnect** + live lines via Redis pub/sub | ✅ verified with a real WS client |
| 🗄️ Full run history + logs in PostgreSQL | ✅ verified live |
| 🛑 Manual trigger refuses to start a second run while one is active | ✅ |
| 🐳 Docker image build + test-in-container + push | ⚠️ implemented, **not run in my test environment** (no Docker daemon) |
| 🚢 SSH deploy with health check and rollback to the previous image | ⚠️ implemented, **not run** (no SSH target) |

> Be upfront about the ⚠️ rows when you present this project. Run a pipeline against a real repo on your
> machine to confirm them — the failure modes (Docker daemon missing) produce clear errors, not silent success.

---

## ⚡ Quick start (Docker)

```bash
git clone https://github.com/YOUR_USERNAME/cicd-pipeline-engine
cd cicd-pipeline-engine
cp .env.example .env          # set GITHUB_WEBHOOK_SECRET to a long random string
docker compose up -d --build
curl http://localhost:8000/health      # {"status":"ok","database":true}
```
API docs: http://localhost:8000/docs · Flower: http://localhost:5555

> ⚠️ **Security:** the worker mounts `/var/run/docker.sock` (needed to build images). That is root-equivalent
> access to the host's Docker. Only run this on a machine you trust, against repositories you trust.

### Without Docker for the app
```bash
docker compose up -d postgres redis
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload                                        # terminal 1
celery -A app.tasks.celery_app worker --loglevel=info -Q pipelines   # terminal 2
```

---

## 📡 Usage

**1. Register a pipeline**
```bash
curl -X POST localhost:8000/api/v1/pipelines -H "Content-Type: application/json" \
  -d '{"name":"my-app","repo_url":"https://github.com/you/your-repo","branch":"main"}'
```

**2. Add a `.cicd.yml` to the root of your repo** (all keys optional)
```yaml
test_command: "pytest -q"     # run inside the built image (default: pytest --tb=short -v)
container_name: my-app        # name on the deploy server
port_map: "8000:8000"         # host:container ports on deploy
```
Unknown keys or invalid YAML fail the run immediately with a clear message.

**3. Trigger it** — manually, or by webhook
```bash
curl -X POST localhost:8000/api/v1/pipelines/PIPELINE_ID/trigger -H "Content-Type: application/json" -d '{"branch":"main"}'
```
GitHub → repo **Settings → Webhooks → Add webhook**:
- Payload URL: `https://YOUR_PUBLIC_HOST/api/v1/webhook/github/PIPELINE_ID`
  (for local development expose port 8000 with a tunnel such as ngrok or cloudflared)
- Content type: `application/json` · Secret: your `GITHUB_WEBHOOK_SECRET` · Event: *push*

Pushes to a branch other than the pipeline's branch are ignored (`200`, nothing queued).

**4. Watch it**
```bash
curl localhost:8000/api/v1/runs/RUN_ID            # status + stored logs
curl "localhost:8000/api/v1/runs/RUN_ID/logs"     # logs only (paginated)
```
```javascript
// live logs — replays history first, then streams new lines
const ws = new WebSocket("ws://localhost:8000/api/v1/ws/runs/RUN_ID/logs");
ws.onmessage = (e) => console.log(JSON.parse(e.data).content);
```

**Optional SSH deploy:** set `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY_PATH` in `.env`. Without them the deploy
step is skipped and logged as such.

---

## 🗂️ Structure
```
app/
├── main.py                    FastAPI app
├── core/{config,security}.py  settings · HMAC webhook verification
├── models/pipeline.py         Pipeline · PipelineRun · PipelineLog
├── services/
│   ├── docker_service.py      build / test-in-container / push
│   ├── ssh_service.py         deploy + health check + rollback
│   └── pipeline_config.py     .cicd.yml loader
├── tasks/pipeline_tasks.py    the pipeline (Celery)
├── websocket/manager.py       connection registry
└── api/routes/pipelines.py    REST + webhook + WebSocket
tests/                         20 tests, no external services needed
```

## 🧪 Tests
```bash
pytest tests/ -v        # 20 tests, ~1 s — uses SQLite, needs no Postgres/Redis/Docker
```

---

## 🐛 Real bugs found and fixed while building this
1. **`.cicd.yml` was documented but never read** — the config field existed but nothing populated it. Now implemented, validated, and tested.
2. **Webhook for an ignored branch returned `202 Accepted`** though nothing was queued — now `200`.
3. **Malformed unhealthy response** — `JSONResponse(503, {...})` had its arguments in the wrong order.
4. **Tests required a live PostgreSQL** because the app connected at import/startup — the session layer now supports SQLite.
5. **`docker-compose` used `localhost` between containers** (would fail inside the network) and the `flower` service's package was missing from `requirements.txt`.

## ⚠️ Known limitations / roadmap
- **No concurrency control for webhook-triggered runs** — two quick pushes can start two builds of the same branch (the manual trigger has a guard). A per-branch Redis lock is the natural next step.
- SSH uses `AutoAddPolicy` (accepts unknown host keys) — pin host keys before using on an untrusted network.
- Builds run on the worker host with access to its Docker daemon — there is no sandboxing of untrusted repos.
- `cancel` revokes the Celery task but does not stop an already-running container.

MIT License
