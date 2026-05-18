"""
HeyGen Streaming Avatar V2 provider — text-in mode.

Backend POSTs text to /v1/streaming.task; HeyGen runs TTS + lip-sync server-
side and streams video/audio to the frontend over LiveKit. No PCM crosses
the WS for HeyGen sessions — ws_interview.py branches on `provider.mode`.

Voice (incl. 3rd-party ElevenLabs) is bound to the interactive avatar in
HeyGen's dashboard, so we never pass `voice` on streaming.task — it's
resolved server-side.

API field names in HeyGen's responses (url vs livekit_url, access_token vs
token) can drift between API versions. Logged responses on first failure
should make any mismatch obvious.
"""
from typing import ClassVar

import httpx
import structlog

from app.avatar.base import AvatarMode, AvatarSessionProvider
from app.config import settings
from app.core.circuit_breaker import CircuitBreaker

log = structlog.get_logger()

_HEYGEN_BASE = "https://api.heygen.com"
_NEW_URL = f"{_HEYGEN_BASE}/v1/streaming.new"
_START_URL = f"{_HEYGEN_BASE}/v1/streaming.start"
_TASK_URL = f"{_HEYGEN_BASE}/v1/streaming.task"
_STOP_URL = f"{_HEYGEN_BASE}/v1/streaming.stop"


class HeyGenSessionProvider(AvatarSessionProvider):
    mode: ClassVar[AvatarMode] = "text"

    def __init__(self, cb: CircuitBreaker) -> None:
        self._cb = cb

    def _headers(self) -> dict[str, str]:
        if not settings.heygen_api_key:
            raise RuntimeError(
                "HEYGEN_API_KEY not configured — HeyGen provider unusable"
            )
        return {
            "X-Api-Key": settings.heygen_api_key,
            "Content-Type": "application/json",
        }

    async def get_session(self) -> dict:
        async def _fetch() -> dict:
            if not settings.heygen_avatar_id:
                raise RuntimeError(
                    "HEYGEN_AVATAR_ID not configured — HeyGen provider unusable"
                )
            async with httpx.AsyncClient(timeout=15.0) as client:
                new_resp = await client.post(
                    _NEW_URL,
                    headers=self._headers(),
                    json={
                        "quality": settings.heygen_quality,
                        "avatar_id": settings.heygen_avatar_id,
                        "version": "v2",
                    },
                )
                new_resp.raise_for_status()
                payload = new_resp.json() or {}
                data = payload.get("data") or {}
                session_id = data.get("session_id")
                if not session_id:
                    log.error("heygen_new_missing_session_id", payload=payload)
                    raise RuntimeError("HeyGen streaming.new returned no session_id")

                start_resp = await client.post(
                    _START_URL,
                    headers=self._headers(),
                    json={"session_id": session_id},
                )
                start_resp.raise_for_status()

                # `session_token` mirrors `access_token` so frontend
                # AvatarInitParams.sessionToken stays the same field across
                # providers; HeyGen-specific fields ride alongside.
                return {
                    "session_id": session_id,
                    "url": data.get("url"),
                    "access_token": data.get("access_token"),
                    "session_token": data.get("access_token"),
                    "ice_servers": data.get("ice_servers") or [],
                }

        return await self._cb.call(_fetch)

    async def speak(self, avatar_session_id: str, text: str) -> None:
        async def _send() -> None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    _TASK_URL,
                    headers=self._headers(),
                    json={
                        "session_id": avatar_session_id,
                        "text": text,
                        "task_type": "repeat",
                        "task_mode": "async",
                    },
                )
                resp.raise_for_status()

        await self._cb.call(_send)

    async def close(self, avatar_session_id: str) -> None:
        async def _stop() -> None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    _STOP_URL,
                    headers=self._headers(),
                    json={"session_id": avatar_session_id},
                )

        try:
            await self._cb.call(_stop)
        except Exception as exc:
            log.warning(
                "heygen_close_failed",
                avatar_session_id=avatar_session_id,
                error=str(exc),
            )
