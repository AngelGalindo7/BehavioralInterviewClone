"""Unit tests for the hand-rolled ElevenLabs stream-input WebSocket client.

These run against a fake local websockets server — they verify the protocol
framing and PCM decode this client controls, NOT ElevenLabs' live behaviour
(message schema, history semantics), which must be validated against the real
endpoint before enabling tts_use_websocket in production.
"""
import asyncio
import base64
import json

import pytest
from websockets.asyncio.server import serve

from app.audio.tts_ws import WsTtsSession
from app.core.exceptions import TTSError


def _session(port: int) -> WsTtsSession:
    return WsTtsSession(
        voice_id="voice-test",
        model_id="eleven_turbo_v2_5",
        output_format="pcm_16000",
        voice_settings={"stability": 0.5},
        api_key="el-test",
        base_url=f"ws://localhost:{port}",
    )


async def _echo_handler(ws):
    """Skip the BOS frame; echo 4 PCM bytes per text chunk; finalise on EOS."""
    async for raw in ws:
        msg = json.loads(raw)
        if "voice_settings" in msg:  # BOS
            continue
        text = msg.get("text")
        if text == "":  # EOS
            await ws.send(json.dumps({"audio": None, "isFinal": True}))
            return
        pcm = text.strip().encode()[:4]
        await ws.send(json.dumps({"audio": base64.b64encode(pcm).decode(), "isFinal": None}))


@pytest.mark.asyncio
async def test_ws_tts_streams_pcm_for_fed_text():
    async with serve(_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        session = _session(port)
        await session.connect()

        async def _produce():
            await session.feed("Hello")
            await session.feed("world")
            await session.end()

        prod = asyncio.create_task(_produce())
        chunks = [c async for c in session.pcm()]
        await prod
        await session.close()

    # Trailing-space-stripped, first 4 bytes per fed chunk; isFinal stopped us.
    assert chunks == [b"Hell", b"worl"]


@pytest.mark.asyncio
async def test_ws_tts_terminates_on_is_final():
    """An immediate EOS yields no audio and the pcm() loop returns cleanly."""
    async with serve(_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        session = _session(port)
        await session.connect()
        await session.end()
        chunks = [c async for c in session.pcm()]
        await session.close()

    assert chunks == []


@pytest.mark.asyncio
async def test_ws_tts_raises_on_error_frame():
    async def _error_handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            if "voice_settings" in msg:
                continue
            await ws.send(json.dumps({"error": "unauthorized"}))
            return

    async with serve(_error_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        session = _session(port)
        await session.connect()
        await session.feed("Hello")
        with pytest.raises(TTSError):
            async for _ in session.pcm():
                pass
        await session.close()


@pytest.mark.asyncio
async def test_ws_tts_feed_after_close_is_noop():
    """A barge-in closes the socket mid-turn; a producer still feeding must not
    raise (it just no-ops) so cancellation unwinds cleanly."""
    async with serve(_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        session = _session(port)
        await session.connect()
        await session.close()
        await session.feed("late")  # must not raise
        await session.end()         # must not raise
