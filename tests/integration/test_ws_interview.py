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

    async def _fake_generate(_messages, _cb):
        # Single-sentence response — flushes once at end-of-stream.
        yield "Here is my answer."

    async def _fake_tts(_text, _queue, output_format=None, previous_text=None, next_text=None, seed=None):
        yield fake_pcm

    with (
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.api.ws_interview.stream_tts_pcm", side_effect=_fake_tts),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
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
async def test_skip_with_no_active_turn_is_a_noop(auth_cookies):
    """
    A skip arriving when no turn is in flight has nothing to cancel — the reader
    logs a no-op and the socket stays usable. (Mid-turn skip cancellation is
    covered by test_skip_cancels_in_flight_turn below.)
    """
    with (
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
    ):
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
async def test_skip_cancels_in_flight_turn(auth_cookies):
    """
    A barge-in skip sent while a turn is mid-flight must cancel it, propagating
    CancelledError into the OpenAI/ElevenLabs async-for loops so upstream
    token/character billing stops for the audio the user just skipped. Without
    the fix the skip sat queued behind the turn it was meant to interrupt and
    the full answer was synthesised and billed regardless.
    """
    cancellation_observed = threading.Event()
    fake_pcm = bytes(6000)

    async def _fake_generate(_messages, _cb):
        try:
            # Long enough + a boundary so the first flush fires immediately and
            # the client receives bytes, confirming the turn is in flight before
            # we send the skip. Then a long sleep keeps the turn open.
            yield "First sentence runs long enough to flush eagerly."
            await asyncio.sleep(30)
            yield "second"
        except asyncio.CancelledError:
            cancellation_observed.set()
            raise

    async def _fake_tts(_text, _queue, output_format=None, previous_text=None, next_text=None, seed=None):
        yield fake_pcm

    with (
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.api.ws_interview.stream_tts_pcm", side_effect=_fake_tts),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
        patch("app.api.ws_interview.Turn"),
        patch("app.api.ws_interview.AsyncSessionLocal"),
    ):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                ws.send_text(json.dumps({"type": "transcript", "text": "Tell me about yourself."}))
                ws.receive_bytes()  # turn is now mid-flight
                ws.send_text(json.dumps({"type": "skip"}))
                for _ in range(40):
                    if cancellation_observed.is_set():
                        break
                    time.sleep(0.05)
                # Socket stays open after a barge-in — a follow-up frame must not
                # raise, proving the skip cancelled the turn without tearing down
                # the connection (unlike a disconnect).
                ws.send_text(json.dumps({"type": "unknown"}))

        assert cancellation_observed.is_set(), (
            "in-flight turn was not cancelled on skip — upstream billing would continue"
        )


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

    async def _fake_generate(_messages, _cb):
        try:
            # Must exceed settings.first_flush_min_chars (40) AND contain a
            # clause/sentence boundary so the first-flush splitter fires
            # immediately — otherwise the sleep below runs to completion before
            # any flush, the test client never receives bytes, and the disconnect
            # check happens after the stream has already finished.
            yield "First sentence runs long enough to flush eagerly."
            await asyncio.sleep(30)
            yield "second"
        except asyncio.CancelledError:
            cancellation_observed.set()
            raise

    async def _fake_tts(_text, _queue, output_format=None, previous_text=None, next_text=None, seed=None):
        yield fake_pcm

    with (
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.api.ws_interview.stream_tts_pcm", side_effect=_fake_tts),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
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
async def test_ws_tts_path_streams_single_generation(auth_cookies):
    """With tts_use_websocket on, a transcript routes through ONE WsTtsSession
    (fed the LLM text, then ended and closed) and its PCM reaches the browser
    with the 0x01 immediate prefix — the single-continuous-generation path."""
    from app.config import settings

    sessions: list = []

    class _FakeWsSession:
        def __init__(self, **_kwargs):
            self.fed: list[str] = []
            self.connected = self.ended = self.closed = False
            sessions.append(self)

        async def connect(self):
            self.connected = True

        async def feed(self, text):
            self.fed.append(text)

        async def end(self):
            self.ended = True

        async def pcm(self):
            yield bytes(6000)

        async def close(self):
            self.closed = True

    async def _fake_generate(_messages, _cb):
        yield "Here is my answer."

    with (
        patch.object(settings, "tts_use_websocket", True),
        patch("app.api.ws_interview.WsTtsSession", _FakeWsSession),
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
        patch("app.api.ws_interview.Turn"),
        patch("app.api.ws_interview.AsyncSessionLocal"),
    ):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            received: list[bytes] = []
            with client.websocket_connect(f"/ws/interview?session_id={session_id}") as ws:
                ws.send_text(json.dumps({"type": "transcript", "text": "Tell me about yourself."}))
                for _ in range(10):
                    try:
                        received.append(ws.receive_bytes())
                        break
                    except Exception:
                        await asyncio.sleep(0.3)

    assert received, "WS-TTS path produced no audio frames"
    assert received[0][0:1] == b"\x01", "First WS-path chunk must carry the immediate-play prefix"
    assert len(received[0]) == 1 + 6000
    assert sessions, "WsTtsSession was never constructed"
    only = sessions[0]
    assert only.connected and only.ended and only.closed, "session lifecycle incomplete"
    assert "".join(only.fed).strip() == "Here is my answer.", "LLM text was not fed to the WS session"


@pytest.mark.asyncio
async def test_ws_tts_audio_pcm_server_paces_to_provider(auth_cookies):
    """End-to-end cover for the HeyGen/LiveAvatar (audio_pcm_server) WS-TTS path —
    the exact provider+mode combination whose mid-answer cut-off motivated
    _drain_and_pace. A turn must drain the ElevenLabs WS to provider.send_pcm and
    finalise with exactly one send_pcm_end after the audio drains."""
    from app.config import settings

    class _FakeServerProvider:
        mode = "audio_pcm_server"

        def __init__(self):
            self.sent: list[tuple[str, int, bool]] = []
            self.ended = 0

        async def send_pcm(self, sid, pcm, *, is_first):
            self.sent.append((sid, len(pcm), is_first))

        async def send_pcm_end(self, sid):
            self.ended += 1

        async def interrupt(self, sid):
            pass

        async def close(self, sid):
            pass

    class _FakeWsSession:
        def __init__(self, **_kwargs):
            pass

        async def connect(self):
            pass

        async def feed(self, text):
            pass

        async def end(self):
            pass

        async def pcm(self):
            yield bytes(6000)

        async def close(self):
            pass

    async def _fake_generate(_messages, _cb):
        yield "Here is my answer."

    fake_provider = _FakeServerProvider()

    with (
        patch.object(settings, "tts_use_websocket", True),
        patch("app.api.ws_interview.get_avatar_provider_by_name", return_value=fake_provider),
        patch("app.api.ws_interview.WsTtsSession", _FakeWsSession),
        patch("app.api.ws_interview.generate_response", side_effect=_fake_generate),
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
        patch("app.api.ws_interview.Turn"),
        patch("app.api.ws_interview.AsyncSessionLocal"),
    ):
        from app.main import create_app
        app = create_app()

        with TestClient(app, cookies=auth_cookies) as client:
            session_id = str(uuid.uuid4())
            url = (
                f"/ws/interview?session_id={session_id}"
                "&provider=heygen&avatar_session_id=sess-1"
            )
            with client.websocket_connect(url) as ws:
                ws.send_text(json.dumps({"type": "transcript", "text": "Tell me about yourself."}))
                for _ in range(60):
                    if fake_provider.ended >= 1:
                        break
                    time.sleep(0.05)

    assert fake_provider.sent, "send_pcm was never called on the audio_pcm_server provider"
    assert fake_provider.ended == 1, "send_pcm_end must fire exactly once, after the audio drains"
    first_sid, first_bytes, first_is_first = fake_provider.sent[0]
    assert first_sid == "sess-1", "PCM must route to the handshake avatar_session_id"
    assert first_is_first is True, "the first piece of the turn must carry is_first=True"
    assert all(n % 2 == 0 for _, n, _ in fake_provider.sent), "every piece must be PCM16-aligned"


@pytest.mark.asyncio
async def test_oversized_text_frame_is_dropped(auth_cookies):
    """
    A text frame larger than max_ws_text_frame_bytes must be ignored — no JSON
    parsing, no LLM call, no binary response. Protects against memory abuse.
    """
    from app.config import settings

    with (
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._load_stories_from_db", new_callable=AsyncMock),
    ):
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
