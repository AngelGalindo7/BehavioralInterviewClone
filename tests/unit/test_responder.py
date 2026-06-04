"""Unit tests for the OpenAI Chat Completions wrapper (generate_response)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.exceptions import CircuitOpenError
from app.llm.responder import generate_response

_MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
]


# ── Stubs matching the chat.completions stream chunk shape ───────────────────

def _chunk(content: str | None):
    """A chat.completions stream chunk exposing .choices[0].delta.content."""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


class _FakeStream:
    def __init__(self, *chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


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
    stream = _FakeStream(_chunk("Hello"), _chunk(", world"))

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.chat.completions.create = AsyncMock(return_value=stream)
        results = [d async for d in generate_response(_MESSAGES, cb)]

    assert results == ["Hello", ", world"]


@pytest.mark.asyncio
async def test_generate_response_skips_empty_and_role_only_chunks():
    cb = CircuitBreaker("test")
    # role-only first chunk (content None) + an empty delta — both skipped
    stream = _FakeStream(_chunk(None), _chunk(""), _chunk("hi"))

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.chat.completions.create = AsyncMock(return_value=stream)
        results = [d async for d in generate_response(_MESSAGES, cb)]

    assert results == ["hi"]


@pytest.mark.asyncio
async def test_generate_response_passes_messages():
    """The exact messages list must reach the API call unchanged."""
    cb = CircuitBreaker("test")
    stream = _FakeStream(_chunk("x"))

    with patch("app.llm.responder._get_client") as mock_get:
        create_mock = AsyncMock(return_value=stream)
        mock_get.return_value.chat.completions.create = create_mock
        _ = [d async for d in generate_response(_MESSAGES, cb)]

    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs["messages"] is _MESSAGES
    assert call_kwargs["stream"] is True


@pytest.mark.asyncio
async def test_generate_response_retries_on_first_token_stall(monkeypatch):
    """A stalled first token aborts that attempt and re-fires; the retry's
    tokens are streamed and the stalled stream is closed (HTTP aborted)."""
    monkeypatch.setattr(settings, "openai_first_token_timeout_s", 0.05)
    monkeypatch.setattr(settings, "openai_first_token_max_attempts", 3)
    cb = CircuitBreaker("test")
    stalled = _HangingStream()
    healthy = _FakeStream(_chunk("hi"))

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.chat.completions.create = AsyncMock(side_effect=[stalled, healthy])
        results = [d async for d in generate_response(_MESSAGES, cb)]

    assert results == ["hi"]
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
        mock_get.return_value.chat.completions.create = AsyncMock(side_effect=streams)
        with pytest.raises(asyncio.TimeoutError):
            async for _ in generate_response(_MESSAGES, cb):
                pass

    assert all(s.closed for s in streams)


@pytest.mark.asyncio
async def test_first_token_stall_counts_as_circuit_failure(monkeypatch):
    """A fully-stalled turn (parks before the first token on every attempt) must
    register as a circuit failure — a created-but-silent stream is not success.
    Enough stalled turns must trip the breaker so it can fast-fail the rest."""
    monkeypatch.setattr(settings, "openai_first_token_timeout_s", 0.02)
    monkeypatch.setattr(settings, "openai_first_token_max_attempts", 1)
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)

    with patch("app.llm.responder._get_client") as mock_get:
        mock_get.return_value.chat.completions.create = AsyncMock(
            side_effect=lambda **_: _HangingStream()
        )
        for _ in range(2):
            with pytest.raises(asyncio.TimeoutError):
                async for _ in generate_response(_MESSAGES, cb):
                    pass

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_generate_response_raises_circuit_open_when_cb_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)

    # Force circuit open
    async def _fail():
        raise RuntimeError("API down")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    with pytest.raises(CircuitOpenError):
        async for _ in generate_response(_MESSAGES, cb):
            pass
