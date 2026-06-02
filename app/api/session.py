import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewSession
from app.deps import get_db

log = structlog.get_logger()

router = APIRouter()


async def close_session_if_active(
    db: AsyncSession, session_id: uuid.UUID
) -> str:
    """
    Mark *session_id* ended if it exists and is still active. Returns one of:
      "ended"      — row found active, ended_at just set
      "already"    — row found but ended_at was already populated (idempotent)
      "not_found"  — no such session

    Used by DELETE /session/{id}, the WS finally block, and the orphan reaper.
    Centralising the "only-set-if-NULL" predicate avoids two cleanup paths
    racing to overwrite each other's ended_at timestamp.
    """
    row = (
        await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return "not_found"
    if row.ended_at is not None:
        return "already"
    await db.execute(
        update(InterviewSession)
        .where(
            InterviewSession.id == session_id,
            InterviewSession.ended_at.is_(None),
        )
        .values(ended_at=datetime.now(timezone.utc).replace(tzinfo=None))
    )
    await db.commit()
    return "ended"


@router.post("/", status_code=201)
async def create_session(db: AsyncSession = Depends(get_db)) -> dict:
    session = InterviewSession()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"session_id": str(session.id)}


@router.delete("/{session_id}")
async def end_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        outcome = await close_session_if_active(db, session_id)
    except Exception as exc:
        log.error("session_end_failed", session_id=str(session_id), error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to end session")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ended"}
