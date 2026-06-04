"""Unit tests for ElevenLabs TTS streaming and history deletion worker."""
import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.audio.tts as tts_module
from app.audio.tts import (
    LATEST_HISTORY_SENTINEL,
    history_delete_worker,
    stream_tts_pcm,
)
from app.config import settings
from app.core.exceptions import TTSError


@pytest.fixture(autouse=True)
def _reset_singleton_client():
    """
    Reset the process-singleton AsyncElevenLabs cache between tests so each
    test's patch("app.audio.tts.AsyncElevenLabs", ...) actually injects the
    mock instead of returning a stale client cached by an earlier test.
    """
    tts_module._client = None
    yield
    tts_module._client = None


# ── Helpers ───────────────────────────────────────────────────────────────────

class _BytesChunk(bytes):
    """A plain bytes subclass — exercises the isinstance(chunk, bytes) path."""


class _AudioAttrChunk:
    """Typed chunk object with an .audio attribute — exercises the fallback path."""

    def __init__(self, audio: bytes):
        self.audio = audio
        self.alignment = None


class _AlignmentChunk:
    """Chunk that carries alignment metadata with a history_item_id."""

    def __init__(self, audio: bytes, history_item_id: str):
        self.audio = audio

        alignment = MagicMock()
        alignment.history_item_id = history_item_id
        self.alignment = alignment


def _make_elevenlabs_mock(*stream_chunks):
    """Return a patched AsyncElevenLabs whose convert_as_stream yields *stream_chunks*."""
    mock_client = MagicMock()

    async def _fake_stream(**kwargs):
        for chunk in stream_chunks:
            yield chunk

    mock_client.text_to_speech.convert_as_stream = _fake_stream
    return mock_client


# ── stream_tts_pcm ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_tts_pcm_yields_raw_bytes():
    queue: asyncio.Queue[str] = asyncio.Queue()
    mock_client = _make_elevenlabs_mock(b"\x00\x01\x02\x03")

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        chunks = [c async for c in stream_tts_pcm("hello", queue)]

    assert chunks == [b"\x00\x01\x02\x03"]


@pytest.mark.asyncio
async def test_stream_tts_pcm_yields_from_audio_attribute():
    queue: asyncio.Queue[str] = asyncio.Queue()
    audio_bytes = b"\xAA\xBB"
    mock_client = _make_elevenlabs_mock(_AudioAttrChunk(audio_bytes))

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        chunks = [c async for c in stream_tts_pcm("hello", queue)]

    assert chunks == [audio_bytes]


@pytest.mark.asyncio
async def test_stream_tts_pcm_skips_chunks_without_audio():
    queue: asyncio.Queue[str] = asyncio.Queue()
    no_audio = MagicMock(spec=[])  # no .audio, not bytes

    async def _fake_stream(**kwargs):
        yield no_audio

    mock_client = MagicMock()
    mock_client.text_to_speech.convert_as_stream = _fake_stream

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        chunks = [c async for c in stream_tts_pcm("hello", queue)]

    assert chunks == []


@pytest.mark.asyncio
async def test_stream_tts_pcm_enqueues_history_item_id():
    queue: asyncio.Queue[str] = asyncio.Queue()
    chunk = _AlignmentChunk(b"\x00", history_item_id="hist-abc")
    mock_client = _make_elevenlabs_mock(chunk)

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        _ = [c async for c in stream_tts_pcm("hello", queue)]

    assert not queue.empty()
    assert await queue.get() == "hist-abc"


@pytest.mark.asyncio
async def test_stream_tts_pcm_enqueues_only_first_history_id():
    """history_item_id is captured once — only the first occurrence is queued."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    chunk_a = _AlignmentChunk(b"\x00", history_item_id="first-id")
    chunk_b = _AlignmentChunk(b"\x01", history_item_id="second-id")
    mock_client = _make_elevenlabs_mock(chunk_a, chunk_b)

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        _ = [c async for c in stream_tts_pcm("hello", queue)]

    assert queue.qsize() == 1
    assert await queue.get() == "first-id"


@pytest.mark.asyncio
async def test_stream_tts_pcm_enqueues_latest_sentinel_when_no_alignment():
    """
    SDK chunks may not surface alignment.history_item_id depending on the
    elevenlabs package version. On a normal completion without an ID, enqueue
    the LATEST sentinel so the worker can fall back to /v1/history's most
    recent item.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    mock_client = _make_elevenlabs_mock(b"\x00\x01")

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        _ = [c async for c in stream_tts_pcm("hello", queue)]

    assert await queue.get() == LATEST_HISTORY_SENTINEL


@pytest.mark.asyncio
async def test_stream_tts_pcm_enqueues_history_id_on_cancellation():
    """
    The captured history_item_id must be enqueued even when the consumer
    cancels mid-stream — without this the synthesis row leaks into ElevenLabs
    Creator-tier history (~2-year retention).
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    blocker = asyncio.Event()

    async def _slow_stream(**_kwargs):
        yield _AlignmentChunk(b"\x00", history_item_id="captured-id")
        await blocker.wait()  # never set; we cancel the consumer instead
        yield _AlignmentChunk(b"\x01", history_item_id="captured-id")

    mock_client = MagicMock()
    mock_client.text_to_speech.convert_as_stream = _slow_stream

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        async def _consume():
            async for _ in stream_tts_pcm("hello", queue):
                pass

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert await queue.get() == "captured-id"


@pytest.mark.asyncio
async def test_stream_tts_pcm_skips_latest_sentinel_on_cancellation():
    """
    On cancellation without a captured ID, do NOT enqueue the LATEST fallback —
    a previous turn's history item could be deleted instead. Cancel-without-ID
    accepts a small leakage risk to avoid worse data loss.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    blocker = asyncio.Event()

    async def _slow_stream(**_kwargs):
        yield b"\x00"  # no alignment
        await blocker.wait()
        yield b"\x01"

    mock_client = MagicMock()
    mock_client.text_to_speech.convert_as_stream = _slow_stream

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        async def _consume():
            async for _ in stream_tts_pcm("hello", queue):
                pass

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert queue.empty()


@pytest.mark.asyncio
async def test_stream_tts_pcm_raises_on_first_chunk_timeout(monkeypatch):
    """A parked first chunk must abort the flush with TTSError instead of
    hanging until the session watchdog, and enqueue nothing (no synthesis row
    is known to exist, so the LATEST sentinel could delete an earlier turn)."""
    monkeypatch.setattr(settings, "elevenlabs_first_chunk_timeout_s", 0.02)
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def _hanging_stream(**_kwargs):
        await asyncio.sleep(10)  # never yields within the timeout window
        yield b"\x00"

    mock_client = MagicMock()
    mock_client.text_to_speech.convert_as_stream = _hanging_stream

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        with pytest.raises(TTSError):
            async for _ in stream_tts_pcm("hello", queue):
                pass

    assert queue.empty()


# ── history_delete_worker ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_delete_worker_calls_delete_for_queued_item():
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put("item-123")

    mock_client = AsyncMock()

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        task = asyncio.create_task(history_delete_worker(queue))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_client.history.delete.assert_called_once_with(history_item_id="item-123")


@pytest.mark.asyncio
async def test_history_delete_worker_processes_multiple_items():
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put("item-1")
    await queue.put("item-2")

    mock_client = AsyncMock()

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        task = asyncio.create_task(history_delete_worker(queue))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert mock_client.history.delete.call_count == 2


@pytest.mark.asyncio
async def test_history_delete_worker_resolves_latest_sentinel_via_get_all():
    """
    A LATEST sentinel triggers history.get_all(page_size=1) and the resolved
    last_history_item_id is the one passed to history.delete.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put(LATEST_HISTORY_SENTINEL)

    mock_client = AsyncMock()
    mock_client.history.get_all.return_value = MagicMock(last_history_item_id="resolved-id")

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        task = asyncio.create_task(history_delete_worker(queue))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    mock_client.history.get_all.assert_called_once_with(page_size=1)
    mock_client.history.delete.assert_called_once_with(history_item_id="resolved-id")


@pytest.mark.asyncio
async def test_history_delete_worker_skips_when_get_all_returns_no_items():
    """
    If get_all returns no items (last_history_item_id empty), the worker logs
    and moves on — no delete call is made.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put(LATEST_HISTORY_SENTINEL)

    mock_client = AsyncMock()
    mock_client.history.get_all.return_value = MagicMock(last_history_item_id="")

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        task = asyncio.create_task(history_delete_worker(queue))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    mock_client.history.get_all.assert_called_once()
    mock_client.history.delete.assert_not_called()


@pytest.mark.asyncio
async def test_history_delete_worker_continues_after_delete_failure():
    """A delete error must not crash the worker — it should move to the next item."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put("bad-item")
    await queue.put("good-item")

    mock_client = AsyncMock()
    mock_client.history.delete.side_effect = [
        RuntimeError("API down"),  # first call fails
        None,                      # second call succeeds
    ]

    with patch("app.audio.tts.AsyncElevenLabs", return_value=mock_client):
        task = asyncio.create_task(history_delete_worker(queue))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert mock_client.history.delete.call_count == 2
