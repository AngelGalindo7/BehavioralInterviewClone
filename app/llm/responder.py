"""
OpenAI Responses API wrapper.

Uses the newer Responses API (not Chat Completions) for stateful multi-turn
chaining via previous_response_id, avoiding re-sending the full message history
on each request.

First-token resilience: OpenAI intermittently parks a request several seconds
before emitting its first token — observed ~20% of requests, independent of
prompt size, while well under rate limits (so it is upstream latency, not
throttling). With no bound the whole turn freezes for that duration. We cap
*time to first token* and, because the stall is intermittent, abort the stalled
request and re-fire — a retry almost always lands on a fast response. Only the
first token is bounded; once tokens flow the stream runs unbounded.
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


def _delta_of(event) -> str | None:
    """Text payload of an output_text.delta event, else None."""
    if getattr(event, "type", None) == "response.output_text.delta":
        return getattr(event, "delta", "") or None
    return None


async def generate_response(
    question: str,
    system_prompt: str,
    previous_response_id: str | None,
    cb: CircuitBreaker,
) -> AsyncIterator[tuple[str, str]]:
    """
    Stream tokens from the OpenAI Responses API.

    Yields (text_delta, response_id) pairs. response_id is populated from the
    first "response.created" event and repeated on subsequent yields so the
    caller can chain the next turn. Time-to-first-token is bounded and retried
    per the module docstring; if every attempt stalls the final TimeoutError
    propagates to the turn-level error handler.
    """

    async def _call():
        params: dict = dict(
            model=settings.openai_response_model,
            instructions=system_prompt,
            input=question,
            stream=True,
            max_output_tokens=settings.openai_max_output_tokens,
            temperature=settings.openai_temperature,
        )
        if previous_response_id:
            params["previous_response_id"] = previous_response_id
        return await _get_client().responses.create(**params)

    timeout = settings.openai_first_token_timeout_s
    max_attempts = settings.openai_first_token_max_attempts
    # Shared with the timeout handler so a stalled attempt can be closed (which
    # aborts the upstream HTTP request) instead of leaking a parked connection.
    # `iter` is the SINGLE iterator used for both the first-token drain and the
    # continued stream, so we never re-enter __aiter__ (which on some stream
    # types restarts from the first event).
    holder: dict[str, object] = {}

    async def _open_and_drain() -> tuple[str, str | None]:
        stream = await cb.call(_call)
        holder["stream"] = stream
        stream_iter = stream.__aiter__()
        holder["iter"] = stream_iter
        response_id = ""
        while True:
            try:
                event = await stream_iter.__anext__()
            except StopAsyncIteration:
                return response_id, None
            if getattr(event, "type", None) == "response.created":
                response_id = event.response.id
            elif (delta := _delta_of(event)) is not None:
                return response_id, delta

    for attempt in range(1, max_attempts + 1):
        holder.clear()
        try:
            response_id, first_delta = await asyncio.wait_for(_open_and_drain(), timeout)
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
            yield first_delta, response_id
        while True:
            try:
                event = await stream_iter.__anext__()
            except StopAsyncIteration:
                break
            if getattr(event, "type", None) == "response.created":
                response_id = event.response.id
            elif (delta := _delta_of(event)) is not None:
                yield delta, response_id
        return
