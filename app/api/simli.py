import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.deps import get_simli_cb

router = APIRouter()

_SIMLI_BASE = "https://api.simli.ai"
_TOKEN_URL = f"{_SIMLI_BASE}/compose/token"
_ICE_URL = f"{_SIMLI_BASE}/compose/ice"


@router.post("/token")
async def get_simli_token(
    simli_cb: CircuitBreaker = Depends(get_simli_cb),
) -> dict:
    """
    Fetch a Simli session token + ICE servers for the v3.x simli-client SDK.
    Both are proxied so the API key stays server-side.
    Called once per interview session, before the WebSocket is opened.
    """

    async def _fetch() -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                _TOKEN_URL,
                headers={"x-simli-api-key": settings.simli_api_key},
                json={
                    "faceId": settings.simli_face_id,
                    "handleSilence": True,
                    "maxSessionLength": settings.simli_max_session_length,
                    "maxIdleTime": settings.simli_max_idle_time,
                    "model": settings.simli_model,
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            ice_resp = await client.get(
                _ICE_URL,
                headers={"x-simli-api-key": settings.simli_api_key},
            )
            ice_resp.raise_for_status()
            ice_servers = ice_resp.json()

            return {
                "session_token": token_data.get("session_token"),
                "ice_servers": ice_servers,
            }

    try:
        data = await simli_cb.call(_fetch)
    except CircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502, detail=f"Simli API error: {exc.response.status_code}"
        ) from exc

    if not data.get("session_token"):
        raise HTTPException(status_code=502, detail="Simli token response missing session_token")

    return data
