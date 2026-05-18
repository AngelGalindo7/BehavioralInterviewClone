import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.avatar.base import AvatarSessionProvider
from app.config import settings
from app.core.exceptions import CircuitOpenError
from app.deps import (
    get_available_avatar_providers,
    get_avatar_provider,
    get_avatar_provider_by_name,
)

router = APIRouter()


@router.get("/providers")
async def list_avatar_providers() -> dict:
    """Names of providers built at startup; used by the frontend UI toggle."""
    return {"providers": get_available_avatar_providers()}


@router.post("/session")
async def get_avatar_session(
    provider_name: str | None = Query(default=None, alias="provider"),
    default_provider: AvatarSessionProvider = Depends(get_avatar_provider),
) -> dict:
    """
    Fetch session credentials for the requested provider (or the configured
    default when ?provider= is omitted). Response always includes "provider"
    so the frontend can branch on the response shape:
      Simli  → {"provider": "simli",  "session_token", "ice_servers"}
      HeyGen → {"provider": "heygen", "session_token", "session_id", "url", "ice_servers"}

    The DI-injected default_provider is retained so existing dependency
    overrides in tests continue to work when no query param is supplied.
    """
    if provider_name is None:
        avatar = default_provider
        resolved_name = settings.avatar_provider
    else:
        avatar = get_avatar_provider_by_name(provider_name)
        if avatar is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown or unconfigured avatar provider: {provider_name!r}",
            )
        resolved_name = provider_name

    try:
        data = await avatar.get_session()
    except CircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Avatar provider error: {exc.response.status_code}",
        ) from exc

    if not data.get("session_token"):
        raise HTTPException(status_code=502, detail="Avatar session response missing session_token")

    return {"provider": resolved_name, **data}
