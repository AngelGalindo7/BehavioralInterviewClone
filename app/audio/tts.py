"""
ElevenLabs TTS streaming with active history deletion.

output_format="pcm_16000" requests raw PCM16 at 16 kHz mono directly from the
API — no backend transcoding (ffmpeg/pydub) needed, preserving CPU headroom on
the EC2 t4g.small instance.

Uses a process-singleton AsyncElevenLabs client so the per-sentence flush pattern
in ws_interview reuses TLS connections instead of paying a fresh handshake per
sentence (which would push first-chunk latency back up).

History deletion approximates Zero Data Retention on the Creator tier: each
synthesised history_item_id is enqueued from a finally block so even a cancelled
stream (interviewer hung up mid-utterance) still gets cleaned up. When the SDK
fails to surface an alignment ID — a known quirk across versions — the stream
enqueues a LATEST sentinel and the worker resolves it via history.get_all,
deleting the most recent item. ElevenLabs Creator tier retains logs for up to
2 years by default, so leakage on cancellation is not acceptable.
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog
from elevenlabs import VoiceSettings
from elevenlabs.client import AsyncElevenLabs

from app.config import settings

log = structlog.get_logger(__name__)

LATEST_HISTORY_SENTINEL = "__LATEST__"

_client: AsyncElevenLabs | None = None


def _get_client() -> AsyncElevenLabs:
    global _client
    if _client is None:
        _client = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
    return _client


async def stream_tts_pcm(
    text: str,
    history_delete_queue: asyncio.Queue[str],
    output_format: str | None = None,
) -> AsyncIterator[bytes]:
    """
    Stream PCM16 audio bytes from ElevenLabs for *text*.

    *output_format* overrides settings.elevenlabs_output_format per-call so
    providers with different sample-rate requirements (Simli: 16 kHz, LiveAvatar
    LITE: 24 kHz) can share this function without forcing a global config.

    Captures history_item_id from the first chunk that exposes alignment data.
    Enqueues for deletion in a finally block so a cancelled stream still cleans
    up. Falls back to a LATEST sentinel only on a normal completion that didn't
    surface an alignment ID — on cancellation we don't enqueue the fallback,
    because no synthesis row may exist yet and we'd risk deleting an unrelated
    earlier turn.
    """
    client = _get_client()
    history_item_id: str | None = None
    completed_normally = False
    chunk_index = 0
    cumulative_bytes = 0
    fmt = output_format or settings.elevenlabs_output_format

    log.debug("tts_stream_start", text_len=len(text), text_preview=text[:60], output_format=fmt)

    try:
        async for chunk in client.text_to_speech.convert_as_stream(
            voice_id=settings.elevenlabs_voice_id,
            text=text,
            model_id=settings.elevenlabs_model_id,
            output_format=fmt,
            voice_settings=VoiceSettings(
                stability=settings.elevenlabs_stability,
                similarity_boost=settings.elevenlabs_similarity_boost,
                style=settings.elevenlabs_style,
                use_speaker_boost=settings.elevenlabs_use_speaker_boost,
            ),
        ):
            audio_bytes: bytes | None = None
            if isinstance(chunk, bytes):
                audio_bytes = chunk
            elif hasattr(chunk, "audio") and chunk.audio:
                audio_bytes = chunk.audio

            if audio_bytes is not None:
                chunk_len = len(audio_bytes)
                is_aligned = chunk_len % 2 == 0
                cumulative_bytes += chunk_len
                log.debug(
                    "tts_raw_chunk",
                    chunk_index=chunk_index,
                    chunk_bytes=chunk_len,
                    is_pcm16_aligned=is_aligned,
                    cumulative_bytes=cumulative_bytes,
                )
                if not is_aligned:
                    log.warning(
                        "tts_raw_chunk_misaligned",
                        chunk_index=chunk_index,
                        chunk_bytes=chunk_len,
                        cumulative_bytes=cumulative_bytes,
                        detail="odd-length ElevenLabs chunk breaks PCM16 sample boundaries — likely cause of static",
                    )
                chunk_index += 1
                yield audio_bytes

            if history_item_id is None and hasattr(chunk, "alignment") and chunk.alignment:
                item_id = getattr(chunk.alignment, "history_item_id", None)
                if item_id:
                    history_item_id = item_id
        completed_normally = True
        log.debug(
            "tts_stream_complete",
            total_chunks=chunk_index,
            total_bytes=cumulative_bytes,
            is_total_pcm16_aligned=cumulative_bytes % 2 == 0,
        )
    finally:
        if history_item_id:
            with contextlib.suppress(asyncio.QueueFull):
                history_delete_queue.put_nowait(history_item_id)
            log.debug("tts_history_item_queued", history_item_id=history_item_id)
        elif completed_normally:
            with contextlib.suppress(asyncio.QueueFull):
                history_delete_queue.put_nowait(LATEST_HISTORY_SENTINEL)
            log.debug("tts_history_fallback_queued")


async def history_delete_worker(queue: asyncio.Queue[str]) -> None:
    """
    Background task that drains the history deletion queue.
    Runs as a long-lived asyncio task started at app startup.

    A LATEST_HISTORY_SENTINEL item triggers a fallback lookup via
    history.get_all(page_size=1) — used when the convert_as_stream chunks
    didn't expose alignment.history_item_id (SDK-version dependent). Safe under
    the project's single-user constraint; would race in a multi-tenant setup.
    """
    client = _get_client()
    while True:
        item_id: str = await queue.get()
        target_id: str = item_id
        try:
            if item_id == LATEST_HISTORY_SENTINEL:
                resp = await client.history.get_all(page_size=1)
                raw_id: str | None = getattr(resp, "last_history_item_id", None) or None
                if not raw_id:
                    log.debug("tts_history_fallback_empty")
                    continue
                target_id = raw_id
            await client.history.delete(history_item_id=target_id)
            log.info("tts_history_deleted", history_item_id=target_id)
        except Exception as exc:
            log.warning(
                "tts_history_delete_failed",
                history_item_id=target_id,
                queued_as=item_id,
                error=str(exc),
            )
        finally:
            queue.task_done()
