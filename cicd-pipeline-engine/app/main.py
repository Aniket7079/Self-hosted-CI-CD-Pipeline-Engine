from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.session import engine, Base
from app.api.routes.pipelines import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    print("✅ CI/CD Engine — database tables ready")
    yield


app = FastAPI(
    title       = "CI/CD Pipeline Engine",
    description = "Self-hosted GitHub-webhook-driven CI/CD runner with Docker builds, WebSocket log streaming, and SSH deployment.",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(router)


@app.get("/", tags=["health"])
def root():
    return {"service": "CI/CD Pipeline Engine", "version": "1.0.0", "docs": "/docs"}


@app.get("/health", tags=["health"])
def health():
    from sqlalchemy import text
    from app.db.session import SessionLocal
    try:
        db = SessionLocal(); db.execute(text("SELECT 1")); db.close()
        return {"status": "ok", "database": True}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": False})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
