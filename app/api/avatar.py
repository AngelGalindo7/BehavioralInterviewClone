import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.avatar.base import AvatarSessionProvider
from app.core.exceptions import CircuitOpenError
from app.deps import get_avatar_provider

router = APIRouter()


@router.post("/session")
async def get_avatar_session(
    provider: AvatarSessionProvider = Depends(get_avatar_provider),
) -> dict:
    """
    Fetch an avatar session token + ICE servers.
    Both are proxied so API keys stay server-side.
    Called once per interview session, before the WebSocket is opened.
    """
    try:
        data = await provider.get_session()
    except CircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502, detail=f"Avatar provider error: {exc.response.status_code}"
        ) from exc

    if not data.get("session_token"):
        raise HTTPException(status_code=502, detail="Avatar session response missing session_token")

    return data
