import asyncio
import tracemalloc
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import FastAPI
from sqlalchemy import text, update

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


def _prewarm_external_clients() -> None:
    """
    Instantiate the OpenAI and ElevenLabs SDK singletons at startup so the first
    interview turn doesn't pay the lazy module-import cost. Constructors are
    cheap and do not make network calls; the actual TLS handshake still happens
    on the first request, but module import + class init is amortised here.
    """
    from app.audio.tts import _get_client as _get_elevenlabs_client
    from app.llm.responder import _get_client as _get_openai_client

    try:
        _get_openai_client()
        log.info("openai_client_prewarmed")
    except Exception as exc:
        log.warning("openai_client_prewarm_failed", error=str(exc))
    try:
        _get_elevenlabs_client()
        log.info("elevenlabs_client_prewarmed")
    except Exception as exc:
        log.warning("elevenlabs_client_prewarm_failed", error=str(exc))


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


async def _orphan_session_reaper() -> None:
    """
    Periodic sweep that closes interview_sessions left with ended_at IS NULL
    past settings.session_max_age_seconds. Defense-in-depth: the WS finally
    block is the primary closer, this catches sessions stranded by backend
    crashes, hung handlers, or rare WS teardown races. Matches Simli's own
    max_session_length so the DB never disagrees with the upstream provider
    about whether a session is still live.
    """
    from app.db.engine import AsyncSessionLocal
    from app.db.models import InterviewSession

    interval = settings.session_reaper_interval_seconds
    max_age = settings.session_max_age_seconds
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    seconds=max_age
                )
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        update(InterviewSession)
                        .where(
                            InterviewSession.ended_at.is_(None),
                            InterviewSession.created_at < cutoff,
                        )
                        .values(
                            ended_at=datetime.now(timezone.utc).replace(tzinfo=None)
                        )
                    )
                    await db.commit()
                if result.rowcount:
                    log.info(
                        "orphan_sessions_reaped",
                        count=result.rowcount,
                        max_age_seconds=max_age,
                    )
            except Exception as exc:
                # Never let a single failed sweep kill the reaper — the next
                # tick will retry. A DB hiccup shouldn't take cleanup offline.
                log.warning("orphan_session_reaper_sweep_failed", error=str(exc))
    except asyncio.CancelledError:
        raise


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
    _prewarm_external_clients()
    # await _warmup_ivfflat_index()  # RAG — see DECISION_LOG.md 05/05/2026

    history_task = asyncio.create_task(history_delete_worker(deps.history_delete_queue))
    tracemalloc_task = asyncio.create_task(_tracemalloc_sampler())
    reaper_task = asyncio.create_task(_orphan_session_reaper())
    log.info("app_startup_complete")

    yield

    for task in (history_task, tracemalloc_task, reaper_task):
        task.cancel()
    for task in (history_task, tracemalloc_task, reaper_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await engine.dispose()
    log.info("app_shutdown_complete")
