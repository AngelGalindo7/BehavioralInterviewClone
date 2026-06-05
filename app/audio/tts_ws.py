"""
ElevenLabs stream-input WebSocket TTS — one continuous generation per turn.

The pinned elevenlabs SDK (1.9.0) exposes only the HTTP convert endpoints
(`convert_as_stream`), which synthesise one request per text. That forces the
sentence-by-sentence flush pattern in ws_interview, where each sentence is an
independent generation with its own prosodic onset/offset — audible as a seam
between sentences even with previous_text/next_text conditioning.

This module hand-rolls the stream-input WebSocket protocol so a whole answer is
ONE generation: text is fed incrementally as the LLM produces it and PCM streams
back concurrently, with the model conditioning across the entire turn. There are
no inter-sentence seams because there are no sentence boundaries in the audio.

Protocol (/v1/text-to-speech/{voice_id}/stream-input):
  BOS  -> {"text": " ", "voice_settings": {...}, "generation_config": {...}, "xi_api_key": "..."}
  text -> {"text": "<chunk> ", "try_trigger_generation": true}   (repeat)
  EOS  -> {"text": ""}
  recv -> {"audio": "<base64 pcm>", "isFinal": null|true, "alignment": ...}

The API key is sent in the BOS frame rather than a header so this client is
independent of the websockets header-kwarg naming (extra_headers vs
additional_headers) across versions.

ZDR: the stream-input protocol does NOT surface a per-generation history_item_id
in its audio frames, so callers approximate Zero Data Retention by deleting the
most-recent history item at end of turn (one generation per turn). Verify
against the live API that streamed generations appear in history and are
deletable via history.delete; if they do not, deletion is a no-op and there is
nothing to leak.
"""
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from websockets.asyncio.client import ClientConnection, connect

from app.config import settings
from app.core.exceptions import TTSError

log = structlog.get_logger(__name__)

# Characters buffered before ElevenLabs starts generating each successive chunk.
# The small first value keeps time-to-first-audio low; later values grow so the
# bulk of the answer is generated with more lookahead (better prosody).
_DEFAULT_CHUNK_SCHEDULE = [50, 120, 160, 290]
# PCM audio frames routinely exceed the websockets 1 MiB default frame cap.
_MAX_FRAME_BYTES = 16 * 1024 * 1024
# Headroom so a paced consumer (LiveAvatar throttles to real time) can buffer
# generated audio instead of back-pressuring ElevenLabs into a slow-read stall.
_MAX_RECV_QUEUE = 512


class WsTtsSession:
    """One ElevenLabs stream-input WebSocket = one continuous generation.

    Lifecycle: ``connect()`` → ``feed()``* → ``end()`` → drain ``pcm()`` →
    ``close()``. ``feed()`` (producer) and ``pcm()`` (consumer) are designed to
    run concurrently from separate tasks; the websockets connection permits one
    concurrent sender and one concurrent receiver.
    """

    def __init__(
        self,
        *,
        voice_id: str,
        model_id: str,
        output_format: str,
        voice_settings: dict[str, Any],
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._voice_id = voice_id
        self._model_id = model_id
        self._output_format = output_format
        self._voice_settings = voice_settings
        self._api_key = api_key or settings.elevenlabs_api_key
        self._base_url = (base_url or settings.elevenlabs_ws_base_url).rstrip("/")
        self._ws: ClientConnection | None = None
        self._closed = False

    @property
    def url(self) -> str:
        return (
            f"{self._base_url}/v1/text-to-speech/{self._voice_id}/stream-input"
            f"?model_id={self._model_id}&output_format={self._output_format}"
        )

    async def connect(self) -> None:
        self._ws = await connect(
            self.url,
            max_size=_MAX_FRAME_BYTES,
            max_queue=_MAX_RECV_QUEUE,
            open_timeout=settings.elevenlabs_first_chunk_timeout_s,
        )
        await self._ws.send(json.dumps({
            "text": " ",
            "voice_settings": self._voice_settings,
            "generation_config": {"chunk_length_schedule": _DEFAULT_CHUNK_SCHEDULE},
            "xi_api_key": self._api_key,
        }))
        log.debug("ws_tts_connected", output_format=self._output_format)

    async def feed(self, text: str) -> None:
        """Send a text chunk. No-op once closed — a barge-in may close the
        socket while the producer task is still feeding."""
        if self._closed or self._ws is None or not text:
            return
        # ElevenLabs treats input as a token stream; a trailing space stops the
        # next chunk being glued onto the previous word.
        payload = text if text.endswith(" ") else text + " "
        await self._ws.send(json.dumps({"text": payload, "try_trigger_generation": True}))

    async def end(self) -> None:
        """Signal end-of-input (EOS). Ignored after close."""
        if self._closed or self._ws is None:
            return
        await self._ws.send(json.dumps({"text": ""}))

    async def pcm(self) -> AsyncIterator[bytes]:
        """Yield raw PCM bytes until the server marks the stream final.

        A normal close (ConnectionClosed) ends the iteration; an explicit error
        frame raises TTSError so the caller's circuit breaker records a failure.
        """
        if self._ws is None:
            raise RuntimeError("WsTtsSession.pcm() called before connect()")
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg.get("error"):
                log.warning("ws_tts_error_frame", error=str(msg.get("error")))
                raise TTSError(f"ElevenLabs stream-input error: {msg.get('error')}")
            audio = msg.get("audio")
            if audio:
                yield base64.b64decode(audio)
            if msg.get("isFinal"):
                break

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            ws, self._ws = self._ws, None
            try:
                await ws.close()
            except Exception:
                pass
