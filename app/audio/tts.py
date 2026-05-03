"""
ElevenLabs TTS streaming with active history deletion.

output_format="pcm_16000" requests raw PCM16 at 16 kHz mono directly from the
API — no backend transcoding (ffmpeg/pydub) needed, preserving CPU headroom on
the EC2 t4g.small instance.

Uses a process-singleton AsyncElevenLabs client so the per-sentence flush pattern
in ws_interview reuses TLS connections instead of paying a fresh handshake per
sentence (which would push first-chunk latency back up).

History deletion approximates Zero Data Retention on the Creator tier: each
synthesised history_item_id is enqueued immediately after streaming and deleted
in the background. ElevenLabs Creator tier retains logs for up to 2 years by
default, so active deletion is essential.
"""
import asyncio
from collections.abc import AsyncIterator

import structlog
from elevenlabs.client import AsyncElevenLabs

from app.config import settings

log = structlog.get_logger()

_client: AsyncElevenLabs | None = None


def _get_client() -> AsyncElevenLabs:
    global _client
    if _client is None:
        _client = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
    return _client


async def stream_tts_pcm(
    text: str,
    history_delete_queue: asyncio.Queue[str],
) -> AsyncIterator[bytes]:
    """
    Stream PCM16 audio bytes from ElevenLabs for *text*.
    After the stream completes, enqueues the history item ID for background deletion.
    """
    client = _get_client()
    history_item_id: str | None = None

    async for chunk in client.text_to_speech.convert_as_stream(
        voice_id=settings.elevenlabs_voice_id,
        text=text,
        model_id=settings.elevenlabs_model_id,
        output_format=settings.elevenlabs_output_format,
        optimize_streaming_latency=4,
    ):
        if isinstance(chunk, bytes):
            yield chunk
        elif hasattr(chunk, "audio") and chunk.audio:
            yield chunk.audio

        if history_item_id is None and hasattr(chunk, "alignment") and chunk.alignment:
            item_id = getattr(chunk.alignment, "history_item_id", None)
            if item_id:
                history_item_id = item_id

    if history_item_id:
        await history_delete_queue.put(history_item_id)
        log.debug("tts_history_item_queued", history_item_id=history_item_id)


async def history_delete_worker(queue: asyncio.Queue[str]) -> None:
    """
    Background task that drains the history deletion queue.
    Runs as a long-lived asyncio task started at app startup.
    """
    client = _get_client()
    while True:
        history_item_id: str = await queue.get()
        try:
            await client.history.delete(history_item_id=history_item_id)
            log.info("tts_history_deleted", history_item_id=history_item_id)
        except Exception as exc:
            log.warning(
                "tts_history_delete_failed",
                history_item_id=history_item_id,
                error=str(exc),
            )
        finally:
            queue.task_done()
