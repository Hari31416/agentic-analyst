from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.redaction import configure_logging
from app.config import get_settings
from app.contracts import CreateLabel, SourceView, ThreadView, WorkspaceView
from app.db.models import Source, Thread, Workspace
from app.db.session import get_session
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    get_settings()
    yield


app = FastAPI(title="Agentic RAG Analyst", version="0.1.0", lifespan=lifespan)
Db = Annotated[Session, Depends(get_session)]


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Any, exc: SQLAlchemyError) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable. Check startup and migrations."},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/readiness")
def readiness(session: Db) -> dict[str, Any]:
    settings = get_settings()
    components: dict[str, dict[str, str]] = {}
    try:
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
        components["database"] = {
            "status": "ready" if version else "unavailable",
            "message": "Migrations applied" if version else "Run make migrate",
        }
    except SQLAlchemyError:
        session.rollback()
        components["database"] = {
            "status": "unavailable",
            "message": "Start PostgreSQL and run make migrate",
        }
    try:
        get_storage(settings).ready()
        components["storage"] = {
            "status": "ready",
            "message": "Durable file storage writable",
        }
    except (OSError, StorageUnavailable):
        components["storage"] = {
            "status": "unavailable",
            "message": "Storage directory is unavailable",
        }
    components["model"] = {
        "status": "configured" if settings.model_configured else "unavailable",
        "message": (
            "Configured; live capability test required"
            if settings.model_configured
            else "Set OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL"
        ),
    }
    components["sandbox"] = {
        "status": "configured" if settings.sandbox_configured else "unavailable",
        "message": (
            "Configured; execution probe required"
            if settings.sandbox_configured
            else "Set SANDBOX_BASE_URL and SANDBOX_IMAGE"
        ),
    }
    return {
        "status": (
            "ready"
            if all(c["status"] == "ready" for c in components.values())
            else "degraded"
        ),
        "components": components,
    }


def workspace_or_404(session: Session, workspace_id: str) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


@app.get("/api/workspaces", response_model=list[WorkspaceView])
def workspaces(session: Db) -> Any:
    return session.scalars(select(Workspace).order_by(Workspace.created_at)).all()


@app.post("/api/workspaces", response_model=WorkspaceView, status_code=201)
def create_workspace(body: CreateLabel, session: Db) -> Any:
    workspace = Workspace(label=body.label)
    session.add(workspace)
    session.commit()
    return workspace


@app.get("/api/workspaces/{workspace_id}/threads", response_model=list[ThreadView])
def threads(workspace_id: str, session: Db) -> Any:
    workspace_or_404(session, workspace_id)
    return session.scalars(
        select(Thread)
        .where(Thread.workspace_id == workspace_id)
        .order_by(Thread.created_at)
    ).all()


@app.post(
    "/api/workspaces/{workspace_id}/threads", response_model=ThreadView, status_code=201
)
def create_thread(workspace_id: str, body: CreateLabel, session: Db) -> Any:
    workspace_or_404(session, workspace_id)
    thread = Thread(workspace_id=workspace_id, label=body.label)
    session.add(thread)
    session.commit()
    return thread


@app.get("/api/workspaces/{workspace_id}/sources", response_model=list[SourceView])
def sources(workspace_id: str, session: Db) -> Any:
    workspace_or_404(session, workspace_id)
    return session.scalars(
        select(Source)
        .where(Source.workspace_id == workspace_id)
        .order_by(Source.created_at)
    ).all()
