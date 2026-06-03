"""
OpenAI Chat Completions wrapper.

Uses Chat Completions, not the Responses API. The Responses API was adding a
flat ~5s to time-to-first-token at our prompt size — measured from the prod box,
identical prompt: Responses API ~5.7s vs Chat Completions ~1.0s. Multi-turn
memory is carried by re-sending the conversation `messages` each turn (the caller
owns the history list) rather than the Responses API's server-side
previous_response_id chain.

First-token resilience: OpenAI intermittently parks a request several seconds
before emitting its first token, independent of prompt size and while well under
rate limits (upstream latency, not throttling). With no bound the whole turn
freezes for that duration. We cap *time to first token* and, because the stall is
intermittent, abort the stalled request and re-fire — a retry almost always lands
on a fast response. Only the first token is bounded; once tokens flow the stream
runs unbounded.
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog
from openai import AsyncOpenAI

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker

log = structlog.get_logger()

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _delta_of(chunk) -> str | None:
    """Text payload of a chat.completions stream chunk, else None.

    The first chunk carries delta.role with no content, and the final chunk
    carries finish_reason with empty content — both map to None here.
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(choices[0].delta, "content", None) or None


async def generate_response(
    messages: list[dict[str, str]],
    cb: CircuitBreaker,
) -> AsyncIterator[str]:
    """
    Stream assistant text deltas from the OpenAI Chat Completions API.

    `messages` is the full chat array (system prompt + prior turns + the current
    user question); the caller owns conversation memory and appends each turn.
    Yields text deltas. Time-to-first-token is bounded and retried per the module
    docstring; if every attempt stalls the final TimeoutError propagates to the
    turn-level error handler.
    """

    async def _call():
        return await _get_client().chat.completions.create(
            model=settings.openai_response_model,
            messages=messages,
            stream=True,
            max_tokens=settings.openai_max_output_tokens,
            temperature=settings.openai_temperature,
        )

    timeout = settings.openai_first_token_timeout_s
    max_attempts = settings.openai_first_token_max_attempts
    # Shared with the timeout handler so a stalled attempt can be closed (which
    # aborts the upstream HTTP request) instead of leaking a parked connection.
    # `iter` is the SINGLE iterator used for both the first-token drain and the
    # continued stream, so we never re-enter __aiter__ (which on some stream
    # types restarts from the first chunk).
    holder: dict[str, object] = {}

    async def _open_and_drain() -> str | None:
        stream = await cb.call(_call)
        holder["stream"] = stream
        stream_iter = stream.__aiter__()
        holder["iter"] = stream_iter
        while True:
            try:
                chunk = await stream_iter.__anext__()
            except StopAsyncIteration:
                return None
            if (delta := _delta_of(chunk)) is not None:
                return delta

    for attempt in range(1, max_attempts + 1):
        holder.clear()
        try:
            first_delta = await asyncio.wait_for(_open_and_drain(), timeout)
        except asyncio.TimeoutError:
            stream = holder.get("stream")
            if stream is not None:
                with contextlib.suppress(Exception):
                    await stream.close()
            log.warning(
                "openai_first_token_timeout",
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_s=timeout,
            )
            if attempt >= max_attempts:
                raise
            continue

        if attempt > 1:
            log.info("openai_first_token_retry_succeeded", attempt=attempt)

        stream_iter = holder["iter"]
        if first_delta:
            yield first_delta
        while True:
            try:
                chunk = await stream_iter.__anext__()
            except StopAsyncIteration:
                break
            if (delta := _delta_of(chunk)) is not None:
                yield delta
        return
