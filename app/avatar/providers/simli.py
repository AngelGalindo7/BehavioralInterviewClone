from typing import ClassVar

import httpx

from app.avatar.base import AvatarMode, AvatarSessionProvider
from app.config import settings
from app.core.circuit_breaker import CircuitBreaker

_SIMLI_BASE = "https://api.simli.ai"
_TOKEN_URL = f"{_SIMLI_BASE}/compose/token"
_ICE_URL = f"{_SIMLI_BASE}/compose/ice"


class SimliSessionProvider(AvatarSessionProvider):
    mode: ClassVar[AvatarMode] = "audio_pcm"

    def __init__(self, cb: CircuitBreaker) -> None:
        self._cb = cb

    async def get_session(self) -> dict:
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

        return await self._cb.call(_fetch)
