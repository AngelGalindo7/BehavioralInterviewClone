import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewSession
from app.deps import get_db

router = APIRouter()


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
    result = await db.execute(
        select(InterviewSession).where(InterviewSession.id == session_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.execute(
        update(InterviewSession)
        .where(InterviewSession.id == session_id)
        .values(ended_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"status": "ended"}
