"""
LiveAvatar LITE provider (mode = "audio_pcm_server").

LiveAvatar is HeyGen's successor product. The legacy HeyGen Streaming Avatar
API (/v1/streaming.*) was sunset on 2026-03-31; all calls now return 401.
LiveAvatar uses a separate API surface and a separate API key. The env vars
and provider name are kept as "heygen" / HEYGEN_* for backwards compatibility
with existing env files, the frontend toggle, and PROJECT_MAP entries — only
the key VALUE has changed (now a LiveAvatar key from app.liveavatar.com).

Flow (per session):
  1. POST /v1/sessions/token   — X-API-KEY auth, returns session_token (JWT).
  2. POST /v1/sessions/start   — Bearer auth with the JWT, returns
        livekit_url + livekit_client_token (frontend joins for video/audio)
        and ws_url (backend connects to push PCM events).
  3. Open ws_url (lazy, on first send_pcm) and hold across turns.
  4. send_pcm():   {"type": "agent.speak",     "audio": "<base64-pcm>", "event_id": "..."}
     send_pcm_end(): {"type": "agent.speak_end", "event_id": "..."}
  5. close(): send agent.interrupt + agent.speak_end, close WS, POST /sessions/stop.

PCM format: 16-bit LE, 24 kHz, mono, base64-encoded. Chunks are forwarded as
ElevenLabs emits them (~4–20 KB) — well under the 1 MB per-event limit and
gives lower latency than batching to 1 s.

WebSocket auth: the OpenAPI spec doesn't document the WS handshake, so we
pass the session_token as a query param (?token=<jwt>) — the most common
pattern for LiveKit-adjacent services. If LiveAvatar rejects this, the first
session attempt will surface the error and we'll switch to subprotocol or
first-message auth.
"""
import asyncio
import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import ClassVar

import httpx
import structlog
from websockets.asyncio.client import ClientConnection, connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.avatar.base import AvatarMode, AvatarSessionProvider
from app.config import settings
from app.core.circuit_breaker import CircuitBreaker

log = structlog.get_logger()

_BASE = "https://api.liveavatar.com"
_TOKEN_URL = f"{_BASE}/v1/sessions/token"
_START_URL = f"{_BASE}/v1/sessions/start"
_STOP_URL = f"{_BASE}/v1/sessions/stop"

# ElevenLabs output format LiveAvatar LITE expects for agent.speak.
LIVEAVATAR_TTS_OUTPUT_FORMAT = "pcm_24000"


@dataclass
class _SessionState:
    session_token: str          # JWT used for /sessions/start, /stop, and WS auth
    ws_url: str
    ws: ClientConnection | None = None
    ws_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Orphaned first byte of a PCM16 sample when ElevenLabs emits an odd-length
    # chunk; prepended to the next chunk so each agent.speak event contains only
    # complete 2-byte samples. Reset to b"" on every send_pcm_end.
    carry: bytes = b""


class HeyGenSessionProvider(AvatarSessionProvider):
    """LiveAvatar LITE — class name retained for backwards compat (see module docstring)."""
    mode: ClassVar[AvatarMode] = "audio_pcm_server"

    def __init__(self, cb: CircuitBreaker) -> None:
        self._cb = cb
        self._sessions: dict[str, _SessionState] = {}

    def _api_key_headers(self) -> dict[str, str]:
        if not settings.heygen_api_key:
            raise RuntimeError(
                "HEYGEN_API_KEY (LiveAvatar key) not configured — LiveAvatar provider unusable"
            )
        return {
            "X-API-KEY": settings.heygen_api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _bearer_headers(jwt: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        }

    async def get_session(self) -> dict:
        async def _fetch() -> dict:
            if not settings.heygen_avatar_id:
                raise RuntimeError(
                    "HEYGEN_AVATAR_ID (LiveAvatar avatar) not configured — provider unusable"
                )
            async with httpx.AsyncClient(timeout=15.0) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    headers=self._api_key_headers(),
                    json={
                        "mode": "LITE",
                        "avatar_id": settings.heygen_avatar_id,
                    },
                )
                token_resp.raise_for_status()
                token_data = (token_resp.json() or {}).get("data") or {}
                session_token = token_data.get("session_token")
                if not session_token:
                    log.error("liveavatar_token_missing_session_token", payload=token_resp.json())
                    raise RuntimeError("LiveAvatar /sessions/token returned no session_token")

                start_resp = await client.post(
                    _START_URL,
                    headers=self._bearer_headers(session_token),
                )
                start_resp.raise_for_status()
                start_data = (start_resp.json() or {}).get("data") or {}
                session_id = start_data.get("session_id")
                livekit_url = start_data.get("livekit_url")
                livekit_client_token = start_data.get("livekit_client_token")
                ws_url = start_data.get("ws_url")
                if not (session_id and livekit_url and livekit_client_token and ws_url):
                    log.error(
                        "liveavatar_start_missing_fields",
                        payload=start_resp.json(),
                        has_session_id=bool(session_id),
                        has_livekit_url=bool(livekit_url),
                        has_livekit_client_token=bool(livekit_client_token),
                        has_ws_url=bool(ws_url),
                    )
                    raise RuntimeError("LiveAvatar /sessions/start missing required fields")

                self._sessions[session_id] = _SessionState(
                    session_token=session_token, ws_url=ws_url
                )
                log.info(
                    "liveavatar_session_created",
                    session_id=session_id,
                    ws_url_host=ws_url.split("?")[0],
                )

                # session_token mirrored as the field name the frontend expects;
                # the LiveKit client uses it as the room access token. url is the
                # LiveKit room URL. session_id routes send_pcm/close on the backend.
                return {
                    "session_id": session_id,
                    "url": livekit_url,
                    "session_token": livekit_client_token,
                    "ice_servers": [],
                }

        return await self._cb.call(_fetch)

    async def _ensure_ws(self, state: _SessionState) -> ClientConnection:
        # Fast path: connection exists and is still open.
        if state.ws is not None and state.ws.close_code is None:
            return state.ws
        async with state.ws_lock:
            if state.ws is not None and state.ws.close_code is None:
                return state.ws
            # Connection was closed by the remote (keepalive timeout) — reconnect.
            if state.ws is not None:
                log.info("liveavatar_ws_reconnecting", reason="keepalive timeout or remote close")
            state.ws = None
            sep = "&" if "?" in state.ws_url else "?"
            url = f"{state.ws_url}{sep}token={state.session_token}"
            state.ws = await ws_connect(url, max_size=2 * 1024 * 1024)
            log.info("liveavatar_ws_connected", ws_url_host=state.ws_url.split("?")[0])
            return state.ws

    async def send_pcm(self, avatar_session_id: str, pcm: bytes, *, is_first: bool) -> None:
        # is_first is accepted for symmetry with the frontend PCM sink but
        # LiveAvatar doesn't require an explicit start marker — the server
        # emits agent.speak_started on its own when the first audio arrives.
        _ = is_first
        state = self._sessions.get(avatar_session_id)
        if state is None:
            raise RuntimeError(f"unknown LiveAvatar session_id: {avatar_session_id!r}")

        # ElevenLabs pcm_24000 returns odd-length chunks ~70% of the time;
        # forwarding them as-is leaves LiveAvatar's PCM16 decoder one byte out
        # of phase and produces a loud screech at end-of-utterance. Hold the
        # orphan byte and prepend to the next chunk so every agent.speak event
        # ends on a complete sample.
        buf = state.carry + pcm
        aligned_len = len(buf) & ~1
        state.carry = buf[aligned_len:]
        if aligned_len == 0:
            return
        aligned_pcm = buf[:aligned_len]

        async def _send() -> None:
            payload = json.dumps({
                "type": "agent.speak",
                "event_id": str(uuid.uuid4()),
                "audio": base64.b64encode(aligned_pcm).decode("ascii"),
            })
            ws = await self._ensure_ws(state)
            try:
                await ws.send(payload)
            except ConnectionClosed:
                # Abrupt disconnect (e.g. keepalive ping timeout with no close frame)
                # leaves close_code=None so _ensure_ws can't detect it; clear and retry once.
                log.info("liveavatar_ws_reconnect", reason="abrupt disconnect on send; reconnecting")
                async with state.ws_lock:
                    state.ws = None
                ws = await self._ensure_ws(state)
                await ws.send(payload)

        await self._cb.call(_send)

    async def send_pcm_end(self, avatar_session_id: str) -> None:
        state = self._sessions.get(avatar_session_id)
        if state is None or state.ws is None:
            return

        # Pad any orphan half-sample with a silence byte and flush it before
        # finalising the utterance, otherwise the decoder hits speak_end with
        # a partial sample queued and produces a noise burst.
        if state.carry:
            padded = state.carry + b"\x00"
            state.carry = b""
            log.info(
                "liveavatar_carry_byte_padded",
                detail="ElevenLabs utterance ended on odd byte; padded final half-sample with silence",
            )

            async def _send_pad() -> None:
                assert state.ws is not None
                await state.ws.send(json.dumps({
                    "type": "agent.speak",
                    "event_id": str(uuid.uuid4()),
                    "audio": base64.b64encode(padded).decode("ascii"),
                }))

            await self._cb.call(_send_pad)

        async def _send() -> None:
            assert state.ws is not None
            await state.ws.send(json.dumps({
                "type": "agent.speak_end",
                "event_id": str(uuid.uuid4()),
            }))

        await self._cb.call(_send)

    async def close(self, avatar_session_id: str) -> None:
        state = self._sessions.pop(avatar_session_id, None)
        if state is None:
            return

        if state.ws is not None:
            try:
                await state.ws.close()
            except Exception as exc:
                log.warning("liveavatar_ws_close_failed", error=str(exc))

        async def _stop() -> None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    _STOP_URL,
                    headers=self._bearer_headers(state.session_token),
                    json={"session_id": avatar_session_id, "reason": "USER_CLOSED"},
                )

        try:
            await self._cb.call(_stop)
        except Exception as exc:
            log.warning(
                "liveavatar_close_failed",
                avatar_session_id=avatar_session_id,
                error=str(exc),
            )
