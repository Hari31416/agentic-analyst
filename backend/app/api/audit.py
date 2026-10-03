from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.inspection import collect_entries, run_export
from app.db.models import Run
from app.db.session import get_session

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]


def _run(session: Session, run_id: str) -> Run:
    run = session.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/api/runs/{run_id}/audit")
def inspect_run_audit(
    run_id: str,
    session: Db,
    action: str | None = None,
    state: str | None = None,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    run = _run(session, run_id)
    entries = collect_entries(session, run, action=action, state=state)
    page = entries[cursor : cursor + limit]
    return {
        "run_id": run_id,
        "state": run.state,
        "total": len(entries),
        "cursor": cursor,
        "limit": limit,
        "next_cursor": (
            cursor + len(page) if cursor + len(page) < len(entries) else None
        ),
        "entries": page,
    }


@router.get("/api/runs/{run_id}/audit/export")
def export_run_audit(run_id: str, session: Db) -> JSONResponse:
    return JSONResponse(
        content=run_export(session, _run(session, run_id)),
        headers={"Content-Disposition": 'attachment; filename="run-audit.json"'},
    )
