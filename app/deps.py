import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker
from app.db.engine import AsyncSessionLocal

# ── Circuit breakers (singleton per-process) ──────────────────────────────────
openai_cb = CircuitBreaker(
    name="openai",
    failure_threshold=settings.cb_failure_threshold,
    recovery_timeout=settings.cb_recovery_timeout,
)

elevenlabs_cb = CircuitBreaker(
    name="elevenlabs",
    failure_threshold=settings.cb_failure_threshold,
    recovery_timeout=settings.cb_recovery_timeout,
)

simli_cb = CircuitBreaker(
    name="simli",
    failure_threshold=settings.cb_failure_threshold,
    recovery_timeout=settings.cb_recovery_timeout,
)

# ── ElevenLabs history-deletion queue (singleton per-process) ─────────────────
history_delete_queue: asyncio.Queue[str] = asyncio.Queue()


# ── FastAPI dependency injectors ──────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def get_openai_cb() -> CircuitBreaker:
    return openai_cb


def get_elevenlabs_cb() -> CircuitBreaker:
    return elevenlabs_cb


def get_simli_cb() -> CircuitBreaker:
    return simli_cb


def get_history_queue() -> asyncio.Queue[str]:
    return history_delete_queue
