import asyncio
import tracemalloc
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.db.engine import engine

log = structlog.get_logger()


async def _verify_db_connection() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("db_connection_verified")


async def _load_stories_from_db() -> None:
    from app.db.engine import AsyncSessionLocal
    from app.db.models import AppSetting
    from app.rag.prompt_builder import set_stories_cache

    async with AsyncSessionLocal() as db:
        row = await db.get(AppSetting, "stories")
        if row:
            set_stories_cache(row.value)
            log.info("stories_loaded_from_db", chars=len(row.value))
        else:
            log.info("stories_not_found_in_db", detail="no stories row yet — corpus empty until first save")


# RAG — IVFFlat warmup retained; uncomment if RAG is re-adopted.
# See DECISION_LOG.md 05/05/2026
# async def _warmup_ivfflat_index() -> None:
#     try:
#         async with engine.connect() as conn:
#             await conn.execute(
#                 text(
#                     "SELECT 1 FROM anecdotes "
#                     "ORDER BY embedding <=> '[0.0]'::vector(1536) "
#                     "LIMIT 1"
#                 )
#             )
#         log.info("ivfflat_index_warmed")
#     except Exception as exc:
#         log.warning("ivfflat_warmup_skipped", reason=str(exc))


async def _tracemalloc_sampler() -> None:
    """
    Periodic memory snapshot off the request path. Sampling on every Nth request
    used to spike single-request latency by tens of ms when the snapshot fired.
    """
    tracemalloc.start()
    try:
        while True:
            await asyncio.sleep(settings.tracemalloc_interval_seconds)
            snapshot = tracemalloc.take_snapshot()
            top = snapshot.statistics("lineno")[:10]
            log.debug("tracemalloc_snapshot", top=[str(s) for s in top])
    except asyncio.CancelledError:
        tracemalloc.stop()
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app import deps
    from app.audio.tts import history_delete_worker

    # Re-bind the history queue to the lifespan's event loop. asyncio.Queue
    # lazily binds to whichever loop touches it first; a module-level singleton
    # leaks across TestClient instances and raises "bound to a different event
    # loop" on the second test.
    deps.history_delete_queue = asyncio.Queue()

    await _verify_db_connection()
    await _load_stories_from_db()
    # await _warmup_ivfflat_index()  # RAG — see DECISION_LOG.md 05/05/2026

    history_task = asyncio.create_task(history_delete_worker(deps.history_delete_queue))
    tracemalloc_task = asyncio.create_task(_tracemalloc_sampler())
    log.info("app_startup_complete")

    yield

    for task in (history_task, tracemalloc_task):
        task.cancel()
    for task in (history_task, tracemalloc_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await engine.dispose()
    log.info("app_shutdown_complete")
