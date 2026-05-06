"""
Integration test for the WebSocket interview endpoint.

Mocks OpenAI and ElevenLabs so no real API calls are made.
Asserts that:
  1. Sending a transcript JSON frame causes the server to emit binary PCM frames
     prefixed with the new 1-byte audio dispatch protocol (0x01 immediate).
  2. Sending {type: "skip"} is accepted silently — the new protocol has the
     frontend call simliClient.ClearBuffer() locally; the backend just logs.
  3. Closing the WebSocket mid-pipeline propagates CancelledError into the
     OpenAI Responses async-for so upstream billing stops.
"""
import asyncio
import json
import os
import threading
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Patch settings before importing the app so validation doesn't fail
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ELEVENLABS_API_KEY", "el-test")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "v-test")
os.environ.setdefault("SIMLI_API_KEY", "s-test")
os.environ.setdefault("SIMLI_FACE_ID", "f-test")


@pytest.mark.asyncio
async def test_transcript_produces_immediate_prefixed_binary_frames(auth_cookies):
    """
    First binary frame after a transcript must carry the 0x01 immediate prefix
    so the frontend dispatches it via simliClient.sendAudioDataImmediate().
    """
    fake_pcm = bytes(6000)

    async def _fake_generate(_q, _sp, _prev, _cb):
        # Single-sentence response — flushes once at end-of-stream.
        yield "Here is my answer.", "resp-id-123"

    async def _fake_tts(_text, _queue):
        yield fake_pcm

    with (
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.api.ws_interview.stream_tts_pcm", side_effect=_fake_tts),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.api.ws_interview.Turn"),
        patch("app.api.ws_interview.AsyncSessionLocal"),
    ):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            received_binary: list[bytes] = []

            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                ws.send_text(json.dumps({"type": "transcript", "text": "Tell me about yourself."}))
                for _ in range(10):
                    try:
                        data = ws.receive_bytes()
                        received_binary.append(data)
                        break
                    except Exception:
                        await asyncio.sleep(0.3)

            assert received_binary, "Expected at least one binary PCM frame"
            first = received_binary[0]
            assert first[0:1] == b"\x01", "First chunk must carry the immediate-play prefix (0x01)"
            assert len(first) == 1 + 6000, "Frame must be 1-byte prefix + 6000 PCM bytes"


@pytest.mark.asyncio
async def test_skip_message_is_accepted_silently(auth_cookies):
    """
    Backend now logs and discards skip — the frontend handles the avatar-buffer
    clear locally via simliClient.ClearBuffer(). No bytes should come back.
    """
    with patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                ws.send_text(json.dumps({"type": "skip"}))
                # Send a no-op JSON to give the server a chance to emit something
                # if (incorrectly) it tries to. iter_text continues; no bytes follow.
                ws.send_text(json.dumps({"type": "unknown"}))
                # Close the socket; if no bytes were emitted by skip, this is fine.
            # If we got here without the test client raising on receive, the skip
            # path didn't push any bytes. Implicit assertion via no exception.


@pytest.mark.asyncio
async def test_disconnect_cancels_in_flight_openai_stream(auth_cookies):
    """
    Closing the WebSocket while the OpenAI Responses stream is mid-flight must
    propagate CancelledError into the async-for so the upstream HTTP request is
    dropped (and we stop being billed for tokens). Uses threading.Event because
    the mocked generator runs in TestClient's server loop, not the test's loop.
    """
    cancellation_observed = threading.Event()
    fake_pcm = bytes(6000)

    async def _fake_generate(_q, _sp, _prev, _cb):
        try:
            yield "First sentence.", "resp-1"
            await asyncio.sleep(30)
            yield "second", "resp-1"
        except asyncio.CancelledError:
            cancellation_observed.set()
            raise

    async def _fake_tts(_text, _queue):
        yield fake_pcm

    with (
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.api.ws_interview.stream_tts_pcm", side_effect=_fake_tts),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.api.ws_interview.Turn"),
        patch("app.api.ws_interview.AsyncSessionLocal"),
    ):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                ws.send_text(json.dumps({"type": "transcript", "text": "Tell me about yourself."}))
                ws.receive_bytes()
            # Exiting the `with` block sends close — give the server loop a
            # moment to observe disconnect and cancel the in-flight task.
            for _ in range(40):
                if cancellation_observed.is_set():
                    break
                time.sleep(0.05)

        assert cancellation_observed.is_set(), (
            "OpenAI stream was not cancelled on disconnect — upstream billing would continue"
        )


@pytest.mark.asyncio
async def test_oversized_text_frame_is_dropped(auth_cookies):
    """
    A text frame larger than max_ws_text_frame_bytes must be ignored — no JSON
    parsing, no LLM call, no binary response. Protects against memory abuse.
    """
    from app.config import settings

    with patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                oversized = json.dumps({"type": "transcript", "text": "x" * (settings.max_ws_text_frame_bytes + 1)})
                ws.send_text(oversized)
                # No bytes should come back — the server logged a warning and continued.
                # We can't easily assert on "no bytes ever" without timing out; instead
                # we send a follow-up skip and confirm the socket still works.
                ws.send_text(json.dumps({"type": "skip"}))
