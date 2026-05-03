"""
OpenAI Responses API wrapper.

Uses the newer Responses API (not Chat Completions) for two reasons:
  1. previous_response_id allows stateful multi-turn chaining without re-sending
     the entire message history on each request, reducing network payload size.
  2. store=False ensures OpenAI does not persist this conversation server-side,
     approximating Zero Data Retention for the sensitive behavioural content.
"""
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
    caller can chain the next turn.
    """

    async def _call():
        params: dict = dict(
            model=settings.openai_response_model,
            instructions=system_prompt,
            input=question,
            store=False,  # ZDR — no server-side retention of behavioural data
            stream=True,
        )
        if previous_response_id:
            params["previous_response_id"] = previous_response_id
        return await _get_client().responses.create(**params)

    stream = await cb.call(_call)

    response_id: str = ""
    async for event in stream:
        event_type = getattr(event, "type", None)
        if event_type == "response.created":
            response_id = event.response.id
        elif event_type == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                yield delta, response_id
