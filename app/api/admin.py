"""Admin endpoints for managing the stories text from a web UI.

Stories text is persisted in the app_settings table (key="stories") so it
survives EC2 redeployments. The in-memory prompt cache is updated on every PUT.

The /anecdotes (chunked RAG corpus) and /reindex (IVFFlat) endpoints are
commented out below — RAG hot path is dormant, see DECISION_LOG.md 05/05/2026.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting
from app.deps import get_db
from app.rag.prompt_builder import set_stories_cache

# RAG — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# import re
# import time
# from datetime import datetime
# from sqlalchemy import delete, func, select, text
# from app.db.engine import engine
# from app.db.models import Anecdote
# from app.rag.embedder import embed_texts
# from ingestion.chunker import chunk_text

router = APIRouter()

_MAX_STORIES_BYTES = 500_000  # 500 KB ceiling for the full corpus file

# RAG — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# _MAX_TITLE_LEN = 200
# _MAX_CONTENT_BYTES = 50_000
# _TITLE_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class StoriesContent(BaseModel):
    content: str = Field(..., min_length=0)


@router.get("/stories")
async def get_stories(db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(AppSetting, "stories")
    return {"content": row.value if row else ""}


@router.put("/stories")
async def save_stories(
    payload: StoriesContent,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if len(payload.content.encode("utf-8")) > _MAX_STORIES_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Content exceeds {_MAX_STORIES_BYTES // 1024} KB cap",
        )
    row = await db.get(AppSetting, "stories")
    if row:
        row.value = payload.content
    else:
        db.add(AppSetting(key="stories", value=payload.content))
    await db.commit()
    set_stories_cache(payload.content)
    return {"status": "ok", "bytes_written": len(payload.content.encode("utf-8"))}


# RAG — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# class AnecdoteUpsert(BaseModel):
#     title: str = Field(..., min_length=1, max_length=_MAX_TITLE_LEN)
#     content: str = Field(..., min_length=1)
#
#
# class AnecdoteSummary(BaseModel):
#     source_file: str
#     chunks: int
#     created_at: datetime
#
#
# def _slugify(title: str) -> str:
#     slug = _TITLE_SLUG_RE.sub("-", title.strip().lower()).strip("-")
#     if not slug:
#         raise HTTPException(status_code=400, detail="Title must contain alphanumerics")
#     return f"{slug}.md"
#
#
# @router.get("/anecdotes")
# async def list_anecdotes(db: AsyncSession = Depends(get_db)) -> list[AnecdoteSummary]:
#     result = await db.execute(
#         select(
#             Anecdote.source_file,
#             func.count().label("chunks"),
#             func.min(Anecdote.created_at).label("created_at"),
#         )
#         .group_by(Anecdote.source_file)
#         .order_by(func.min(Anecdote.created_at).desc())
#     )
#     return [
#         AnecdoteSummary(source_file=row.source_file, chunks=row.chunks, created_at=row.created_at)
#         for row in result.all()
#     ]
#
#
# @router.put("/anecdotes")
# async def upsert_anecdote(
#     payload: AnecdoteUpsert,
#     db: AsyncSession = Depends(get_db),
# ) -> dict:
#     if len(payload.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
#         raise HTTPException(
#             status_code=413,
#             detail=f"Content exceeds {_MAX_CONTENT_BYTES // 1024} KB cap",
#         )
#
#     source_file = _slugify(payload.title)
#     chunks = chunk_text(payload.content)
#     if not chunks:
#         raise HTTPException(
#             status_code=400,
#             detail="No chunks produced — content is empty after normalization",
#         )
#
#     await db.execute(delete(Anecdote).where(Anecdote.source_file == source_file))
#
#     try:
#         embeddings = await embed_texts(chunks)
#     except Exception as exc:  # noqa: BLE001 — surface upstream errors as 502
#         raise HTTPException(
#             status_code=502,
#             detail=f"Embedding API error: {exc}",
#         ) from exc
#
#     rows = [
#         Anecdote(content=chunk, source_file=source_file, embedding=embedding)
#         for chunk, embedding in zip(chunks, embeddings)
#     ]
#
#     db.add_all(rows)
#     await db.commit()
#     return {"source_file": source_file, "chunks_inserted": len(rows)}
#
#
# @router.delete("/anecdotes/{source_file}")
# async def delete_anecdote(
#     source_file: str,
#     db: AsyncSession = Depends(get_db),
# ) -> dict:
#     result = await db.execute(
#         delete(Anecdote).where(Anecdote.source_file == source_file)
#     )
#     await db.commit()
#     if result.rowcount == 0:
#         raise HTTPException(status_code=404, detail=f"No anecdote: {source_file}")
#     return {"source_file": source_file, "chunks_deleted": result.rowcount}
#
#
# @router.post("/reindex")
# async def reindex() -> dict:
#     """Drop and rebuild the IVFFlat index, then VACUUM ANALYZE. Briefly pauses
#     other queries — don't run during a live interview."""
#     if str(engine.url).startswith("sqlite"):
#         raise HTTPException(status_code=501, detail="Reindex unsupported on sqlite")
#
#     started = time.monotonic()
#     async with engine.begin() as conn:
#         await conn.execute(text("DROP INDEX IF EXISTS anecdotes_embedding_ivfflat_idx"))
#         await conn.execute(text("""
#             CREATE INDEX anecdotes_embedding_ivfflat_idx
#             ON anecdotes
#             USING ivfflat (embedding vector_cosine_ops)
#             WITH (lists = 100)
#         """))
#     # VACUUM cannot run inside a transaction.
#     autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
#     async with autocommit_engine.connect() as conn:
#         await conn.execute(text("VACUUM ANALYZE anecdotes"))
#     return {"status": "ok", "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
