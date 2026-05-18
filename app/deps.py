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

# ── Avatar session providers (singletons per-process) ─────────────────────────
# Per-provider circuit breakers: a Simli outage must not open HeyGen's breaker
# and vice versa. Provider names also appear in CB log lines for observability.
def _build_avatar_providers() -> dict[str, "AvatarSessionProvider"]:  # noqa: F821
    from app.avatar.base import AvatarSessionProvider
    from app.avatar.providers.simli import SimliSessionProvider

    providers: dict[str, AvatarSessionProvider] = {}

    simli_cb = CircuitBreaker(
        name="simli",
        failure_threshold=settings.cb_failure_threshold,
        recovery_timeout=settings.cb_recovery_timeout,
    )
    providers["simli"] = SimliSessionProvider(simli_cb)

    # HeyGen is opt-in: only register the provider if its required config is
    # set. Lets existing deployments without HeyGen credentials keep starting
    # cleanly, and the /avatar/session endpoint returns 400 if a client asks
    # for an unregistered provider.
    if settings.heygen_api_key and settings.heygen_avatar_id:
        from app.avatar.providers.heygen import HeyGenSessionProvider

        heygen_cb = CircuitBreaker(
            name="heygen",
            failure_threshold=settings.cb_failure_threshold,
            recovery_timeout=settings.cb_recovery_timeout,
        )
        providers["heygen"] = HeyGenSessionProvider(heygen_cb)

    return providers


_avatar_providers = _build_avatar_providers()

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


def get_avatar_provider():
    """
    Default avatar provider, used when the caller doesn't specify ?provider=.
    Honors settings.avatar_provider when available; otherwise falls back to
    whichever provider is registered (Simli is always registered).
    """
    name = settings.avatar_provider
    if name in _avatar_providers:
        return _avatar_providers[name]
    return next(iter(_avatar_providers.values()))


def get_avatar_provider_by_name(name: str):
    """Return the named provider, or None if not registered."""
    return _avatar_providers.get(name)


def get_available_avatar_providers() -> list[str]:
    """Names of providers built at startup. Used by the frontend toggle."""
    return list(_avatar_providers.keys())


def get_history_queue() -> asyncio.Queue[str]:
    return history_delete_queue
