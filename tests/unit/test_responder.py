"""Unit tests for the OpenAI Responses API wrapper (generate_response)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.llm.responder import generate_response


# ── Event stubs matching the Responses API stream format ─────────────────────

class _CreatedEvent:
    type = "response.created"

    def __init__(self, response_id: str):
        self.response = MagicMock()
        self.response.id = response_id


class _DeltaEvent:
    type = "response.output_text.delta"

    def __init__(self, delta: str):
        self.delta = delta


class _OtherEvent:
    type = "response.done"


class _FakeStream:
    def __init__(self, *events):
        self._events = events

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event


class _HangingStream:
    """Stream whose first token never arrives within the timeout window."""

    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(10)  # always exceeds the test's first-token timeout
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


# ── generate_response ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_response_yields_text_deltas():
    cb = CircuitBreaker("test")
    stream = _FakeStream(
        _CreatedEvent("resp-001"),
        _DeltaEvent("Hello"),
        _DeltaEvent(", world"),
    )

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(return_value=stream)

        results = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    assert len(results) == 2
    assert results[0][0] == "Hello"
    assert results[1][0] == ", world"


@pytest.mark.asyncio
async def test_generate_response_captures_response_id_from_created_event():
    cb = CircuitBreaker("test")
    stream = _FakeStream(
        _CreatedEvent("resp-abc"),
        _DeltaEvent("text"),
    )

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(return_value=stream)

        results = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    assert results[0][1] == "resp-abc"


@pytest.mark.asyncio
async def test_generate_response_skips_empty_deltas():
    cb = CircuitBreaker("test")
    stream = _FakeStream(
        _CreatedEvent("resp-001"),
        _DeltaEvent(""),    # empty — must be skipped
        _DeltaEvent("hi"),
    )

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(return_value=stream)

        results = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    assert len(results) == 1
    assert results[0][0] == "hi"


@pytest.mark.asyncio
async def test_generate_response_ignores_unrelated_event_types():
    cb = CircuitBreaker("test")
    stream = _FakeStream(
        _OtherEvent(),      # should produce no yield
        _DeltaEvent("ok"),
    )

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(return_value=stream)

        results = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    # response_id is "" because no created event arrived before the delta
    assert len(results) == 1
    assert results[0][0] == "ok"
    assert results[0][1] == ""


@pytest.mark.asyncio
async def test_generate_response_passes_previous_response_id():
    """previous_response_id should appear in the params sent to the API."""
    cb = CircuitBreaker("test")
    stream = _FakeStream(_CreatedEvent("resp-002"), _DeltaEvent("x"))

    with patch("app.llm.responder._get_client") as mock_get:
        create_mock = AsyncMock(return_value=stream)
        mock_get.return_value.responses.create = create_mock

        _ = [(d, r) async for d, r in generate_response("q", "sys", "prev-id-99", cb)]

    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs.get("previous_response_id") == "prev-id-99"


@pytest.mark.asyncio
async def test_generate_response_omits_previous_response_id_when_none():
    cb = CircuitBreaker("test")
    stream = _FakeStream(_CreatedEvent("resp-001"), _DeltaEvent("y"))

    with patch("app.llm.responder._get_client") as mock_get:
        create_mock = AsyncMock(return_value=stream)
        mock_get.return_value.responses.create = create_mock

        _ = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    call_kwargs = create_mock.call_args.kwargs
    assert "previous_response_id" not in call_kwargs


@pytest.mark.asyncio
async def test_generate_response_omits_store():
    """store must not be sent — store=False breaks previous_response_id chaining."""
    cb = CircuitBreaker("test")
    stream = _FakeStream(_DeltaEvent("z"))

    with patch("app.llm.responder._get_client") as mock_get:
        create_mock = AsyncMock(return_value=stream)
        mock_get.return_value.responses.create = create_mock

        _ = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    call_kwargs = create_mock.call_args.kwargs
    assert "store" not in call_kwargs


@pytest.mark.asyncio
async def test_generate_response_retries_on_first_token_stall(monkeypatch):
    """A stalled first token aborts that attempt and re-fires; the retry's
    tokens are streamed and the stalled stream is closed (HTTP aborted)."""
    monkeypatch.setattr(settings, "openai_first_token_timeout_s", 0.05)
    monkeypatch.setattr(settings, "openai_first_token_max_attempts", 3)
    cb = CircuitBreaker("test")
    stalled = _HangingStream()
    healthy = _FakeStream(_CreatedEvent("resp-retry"), _DeltaEvent("hi"))

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(side_effect=[stalled, healthy])
        results = [(d, r) async for d, r in generate_response("q", "sys", None, cb)]

    assert results == [("hi", "resp-retry")]
    assert stalled.closed is True  # stalled request was torn down, not leaked


@pytest.mark.asyncio
async def test_generate_response_raises_when_all_attempts_stall(monkeypatch):
    """If every attempt stalls, the final TimeoutError propagates and every
    stalled stream is closed."""
    monkeypatch.setattr(settings, "openai_first_token_timeout_s", 0.05)
    monkeypatch.setattr(settings, "openai_first_token_max_attempts", 2)
    cb = CircuitBreaker("test")
    streams = [_HangingStream(), _HangingStream()]

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.responses.create = AsyncMock(side_effect=streams)
        with pytest.raises(asyncio.TimeoutError):
            async for _ in generate_response("q", "sys", None, cb):
                pass

    assert all(s.closed for s in streams)


@pytest.mark.asyncio
async def test_generate_response_raises_circuit_open_when_cb_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)

    # Force circuit open
    async def _fail():
        raise RuntimeError("API down")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    with pytest.raises(CircuitOpenError):
        async for _ in generate_response("q", "sys", None, cb):
            pass
