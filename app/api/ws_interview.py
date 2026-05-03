"""
WebSocket /ws/interview — the real-time interview orchestration core.

Protocol (client → server, JSON text frames, capped at settings.max_ws_text_frame_bytes):
  {"type": "transcript", "text": "<interviewer question>"}
  {"type": "skip"}   — the frontend already called simliClient.ClearBuffer()
                       locally; this just informs the backend (logged for now,
                       mid-pipeline cancellation is not yet wired).

Protocol (server → client, binary frames):
  1-byte prefix + raw PCM16 LE audio at 16 kHz.
    0x01 → frontend dispatches to sendAudioDataImmediate (first chunk per utterance)
    0x00 → frontend dispatches to sendAudioData (subsequent chunks)

TTFB budget (target ≤ 630 ms):
  Embed (~80ms) + IVFFlat (~100ms) + OpenAI first token (~300ms)
  + ElevenLabs first PCM chunk (~150ms) ≈ 630ms

  Achievable only if LLM tokens are pipelined into TTS sentence-by-sentence
  rather than buffered to completion. _split_at_boundary + _flush below do that:
  as soon as the pending text crosses a sentence boundary (or exceeds
  sentence_boundary_max_chars on a runaway sentence), the partial text is sent
  to ElevenLabs and the resulting PCM is forwarded to the browser immediately.
"""
import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.audio.chunker import iter_pcm_chunks
from app.audio.tts import stream_tts_pcm
from app.avatar.simli_client import AUDIO_IMMEDIATE_PREFIX, AUDIO_NORMAL_PREFIX
from app.config import settings
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.db.engine import AsyncSessionLocal
from app.db.models import Turn
from app.deps import get_elevenlabs_cb, get_history_queue, get_openai_cb
from app.llm.responder import generate_response
from app.rag.embedder import embed_text
from app.rag.prompt_builder import build_system_prompt
from app.rag.retriever import retrieve_anecdotes

log = structlog.get_logger()
router = APIRouter()

_FALLBACK_PCM = bytes(settings.pcm_chunk_bytes)

# Sentence-ending punctuation followed by whitespace (or EOL). The lookahead
# avoids matching decimals like "3.14"; "e.g. " false-positives are tolerable.
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s)|[.!?]$")


def _split_at_boundary(text: str, max_chars: int) -> tuple[str, str]:
    """
    Return (to_flush, remaining). Prefer the latest sentence boundary so each
    flush gives ElevenLabs more text per call (better prosody, fewer requests).
    Force-flush the whole buffer if no boundary found and len >= max_chars.
    """
    last_match = None
    for m in _SENTENCE_BOUNDARY.finditer(text):
        last_match = m
    if last_match is not None:
        end = last_match.end()
        return text[:end], text[end:].lstrip()
    if len(text) >= max_chars:
        return text, ""
    return "", text


async def _send_audio_chunks(
    websocket: WebSocket,
    pcm_iter: AsyncIterator[bytes],
    first_chunk_state: dict,
) -> None:
    """
    Forward chunked PCM to the browser with the 1-byte dispatch prefix.
    `first_chunk_state["is_first"]` is mutated to False after the first send so
    later sentences within the same utterance use the buffered prefix.
    """
    async for tts_bytes in pcm_iter:
        for pcm_chunk in iter_pcm_chunks(tts_bytes, settings.pcm_chunk_bytes):
            if first_chunk_state["is_first"]:
                payload = AUDIO_IMMEDIATE_PREFIX + pcm_chunk
                first_chunk_state["is_first"] = False
            else:
                payload = AUDIO_NORMAL_PREFIX + pcm_chunk
            await websocket.send_bytes(payload)


async def _persist_turn(
    session_id: uuid.UUID,
    sequence: int,
    question: str,
    response: str,
    response_id: str | None,
    ttfb_ms: float,
) -> bool:
    """
    Open a short-lived DB session, write the Turn, commit. Holding the
    connection only for the write keeps the per-worker pool free for concurrent
    interviews. Returns True on successful persist.
    """
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                Turn(
                    session_id=session_id,
                    sequence=sequence,
                    question_text=question,
                    response_text=response,
                    openai_response_id=response_id,
                    ttfb_ms=ttfb_ms,
                )
            )
            await db.commit()
        return True
    except Exception as exc:
        log.error(
            "turn_persist_failed",
            session_id=str(session_id),
            sequence=sequence,
            error=str(exc),
        )
        return False


async def _handle_transcript(
    websocket: WebSocket,
    question: str,
    session_id: uuid.UUID,
    sequence: int,
    previous_response_id: str | None,
    openai_cb: CircuitBreaker,
    elevenlabs_cb: CircuitBreaker,
    history_queue: asyncio.Queue[str],
) -> tuple[str | None, int]:
    """
    Run the full pipeline for one interviewer question.
    next_sequence only advances if the turn was successfully persisted, so
    the turns table never has gaps caused by mid-pipeline failures.
    """
    t0 = time.monotonic()
    first_byte_logged = False

    try:
        embedding = await openai_cb.call(lambda: embed_text(question))
    except CircuitOpenError:
        log.error("openai_cb_open_on_embed", session_id=str(session_id))
        await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
        return previous_response_id, sequence

    async with AsyncSessionLocal() as db:
        anecdotes = await retrieve_anecdotes(db, embedding)
    system_prompt = build_system_prompt(anecdotes)

    full_response = ""
    pending_text = ""
    last_response_id = previous_response_id or ""
    first_chunk_state = {"is_first": True}

    async def _flush(text_to_speak: str) -> None:
        nonlocal first_byte_logged
        if not text_to_speak.strip():
            return
        await _send_audio_chunks(
            websocket,
            stream_tts_pcm(text_to_speak, history_queue),
            first_chunk_state,
        )
        if not first_byte_logged:
            first_byte_logged = True
            log.info(
                "ttfb_first_byte",
                session_id=str(session_id),
                sequence=sequence,
                ttfb_ms=round((time.monotonic() - t0) * 1000, 1),
            )

    try:
        await elevenlabs_cb.check()
        async for delta, resp_id in generate_response(
            question, system_prompt, previous_response_id, openai_cb
        ):
            full_response += delta
            pending_text += delta
            if resp_id:
                last_response_id = resp_id
            to_flush, pending_text = _split_at_boundary(
                pending_text, settings.sentence_boundary_max_chars
            )
            if to_flush:
                await _flush(to_flush)
        if pending_text.strip():
            await _flush(pending_text)
        await elevenlabs_cb.on_success()
    except CircuitOpenError:
        log.error("circuit_open_during_turn", session_id=str(session_id))
        await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
        return previous_response_id, sequence
    except Exception as exc:
        log.error("turn_pipeline_error", session_id=str(session_id), error=str(exc))
        await elevenlabs_cb.on_failure(exc)
        await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
        return previous_response_id, sequence

    if not full_response.strip():
        return last_response_id or None, sequence

    ttfb_ms = (time.monotonic() - t0) * 1000
    log.info(
        "turn_complete",
        session_id=str(session_id),
        sequence=sequence,
        ttfb_ms=round(ttfb_ms, 1),
        question_preview=question[:60],
    )

    persisted = await _persist_turn(
        session_id=session_id,
        sequence=sequence,
        question=question,
        response=full_response,
        response_id=last_response_id or None,
        ttfb_ms=ttfb_ms,
    )
    next_sequence = sequence + 1 if persisted else sequence
    return last_response_id or None, next_sequence


@router.websocket("/ws/interview")
async def interview_ws(
    websocket: WebSocket,
    session_id: uuid.UUID,
    openai_cb: CircuitBreaker = Depends(get_openai_cb),
    elevenlabs_cb: CircuitBreaker = Depends(get_elevenlabs_cb),
    history_queue: asyncio.Queue[str] = Depends(get_history_queue),
) -> None:
    await websocket.accept()
    log.info("ws_connected", session_id=str(session_id))

    previous_response_id: str | None = None
    sequence = 0
    max_bytes = settings.max_ws_text_frame_bytes

    try:
        async for raw in websocket.iter_text():
            if len(raw) > max_bytes:
                log.warning(
                    "ws_frame_too_large",
                    session_id=str(session_id),
                    size=len(raw),
                    limit=max_bytes,
                )
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ws_invalid_json", raw=raw[:200])
                continue

            msg_type = msg.get("type")

            if msg_type == "skip":
                log.debug("ws_skip_received", session_id=str(session_id))
                continue

            if msg_type != "transcript":
                log.debug("ws_unknown_message_type", msg_type=msg_type)
                continue

            question = (msg.get("text") or "").strip()
            if not question:
                continue

            previous_response_id, sequence = await _handle_transcript(
                websocket=websocket,
                question=question,
                session_id=session_id,
                sequence=sequence,
                previous_response_id=previous_response_id,
                openai_cb=openai_cb,
                elevenlabs_cb=elevenlabs_cb,
                history_queue=history_queue,
            )

    except WebSocketDisconnect:
        log.info("ws_disconnected", session_id=str(session_id))
    except Exception as exc:
        log.error("ws_unhandled_error", session_id=str(session_id), error=str(exc))
