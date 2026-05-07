"""
WebSocket /ws/interview — the real-time interview orchestration core.

Protocol (client → server, JSON text frames, capped at settings.max_ws_text_frame_bytes):
  {"type": "transcript", "text": "<interviewer question>"}
  {"type": "skip"}   — the frontend already called simliClient.ClearBuffer()
                       locally; this just informs the backend (logged for now,
                       skip-driven mid-pipeline cancellation is not yet wired).

Protocol (server → client, binary frames):
  1-byte prefix + raw PCM16 LE audio at 16 kHz.
    0x01 → frontend dispatches to sendAudioDataImmediate (first chunk per utterance)
    0x00 → frontend dispatches to sendAudioData (subsequent chunks)

TTFB budget (target ≤ 450 ms):
  Stories pre-loaded from data/stories.md at startup (0ms) + OpenAI first token (~300ms)
  + ElevenLabs first PCM chunk (~150ms) ≈ 450ms

  Achievable only if LLM tokens are pipelined into TTS sentence-by-sentence
  rather than buffered to completion. _split_at_boundary + _flush below do that:
  as soon as the pending text crosses a sentence boundary (or exceeds
  sentence_boundary_max_chars on a runaway sentence), the partial text is sent
  to ElevenLabs and the resulting PCM is forwarded to the browser immediately.

Disconnect cancellation:
  Reader and processor run inside an asyncio.TaskGroup. When the client closes
  the socket, iter_text() raises WebSocketDisconnect in the reader, which cancels
  the processor; CancelledError propagates through _handle_transcript into the
  OpenAI and ElevenLabs async-for loops, closing the underlying httpx responses
  and stopping upstream token/character billing for the in-flight turn.
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
from app.rag.prompt_builder import build_system_prompt

# RAG — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# from app.rag.embedder import embed_text
# from app.rag.retriever import retrieve_anecdotes

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
) -> tuple[int, int]:
    """
    Forward chunked PCM to the browser with the 1-byte dispatch prefix.
    `first_chunk_state["is_first"]` is mutated to False after the first send so
    later sentences within the same utterance use the buffered prefix.
    Returns (frames_sent, total_pcm_bytes_sent).
    """
    frames_sent = 0
    total_pcm_bytes = 0
    misaligned_frames = 0
    raw_chunk_index = 0

    async for tts_bytes in pcm_iter:
        raw_len = len(tts_bytes)
        raw_aligned = raw_len % 2 == 0
        log.debug(
            "ws_tts_raw_chunk_received",
            raw_chunk_index=raw_chunk_index,
            raw_bytes=raw_len,
            is_pcm16_aligned=raw_aligned,
        )
        if not raw_aligned:
            log.warning(
                "ws_tts_raw_chunk_misaligned_before_slice",
                raw_chunk_index=raw_chunk_index,
                raw_bytes=raw_len,
                detail="odd-length ElevenLabs chunk will produce misaligned output chunks",
            )
        raw_chunk_index += 1

        for pcm_chunk in iter_pcm_chunks(tts_bytes, settings.pcm_chunk_bytes):
            pcm_len = len(pcm_chunk)
            is_immediate = first_chunk_state["is_first"]
            if is_immediate:
                payload = AUDIO_IMMEDIATE_PREFIX + pcm_chunk
                first_chunk_state["is_first"] = False
            else:
                payload = AUDIO_NORMAL_PREFIX + pcm_chunk

            is_pcm_aligned = pcm_len % 2 == 0
            if not is_pcm_aligned:
                misaligned_frames += 1
                log.warning(
                    "ws_send_frame_misaligned",
                    frame_index=frames_sent,
                    pcm_bytes=pcm_len,
                    total_payload_bytes=len(payload),
                    is_immediate=is_immediate,
                    detail="odd-length PCM frame sent to browser — Simli will decode a broken half-sample, causing static",
                )
            else:
                log.debug(
                    "ws_send_frame",
                    frame_index=frames_sent,
                    pcm_bytes=pcm_len,
                    is_immediate=is_immediate,
                )

            await websocket.send_bytes(payload)
            frames_sent += 1
            total_pcm_bytes += pcm_len

    log.info(
        "ws_audio_chunks_sent",
        frames_sent=frames_sent,
        total_pcm_bytes=total_pcm_bytes,
        misaligned_frames=misaligned_frames,
        is_total_aligned=total_pcm_bytes % 2 == 0,
    )
    return frames_sent, total_pcm_bytes


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

    Cancellation: when the enclosing task is cancelled (WS disconnect), the
    CancelledError propagates through the OpenAI and ElevenLabs async-for loops,
    closing their underlying httpx responses. tts.py's finally block enqueues
    any captured history_item_id before re-raising, so even a cancelled stream
    cleans up its ElevenLabs history row.
    """
    t0 = time.monotonic()
    first_byte_logged = False

    # RAG path — retained for re-adoption; see DECISION_LOG.md 05/05/2026
    # try:
    #     embedding = await openai_cb.call(lambda: embed_text(question))
    # except CircuitOpenError:
    #     log.error("openai_cb_open_on_embed", session_id=str(session_id))
    #     await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
    #     return previous_response_id, sequence
    # async with AsyncSessionLocal() as db:
    #     anecdotes = await retrieve_anecdotes(db, embedding)
    # system_prompt = build_system_prompt(anecdotes)

    system_prompt = build_system_prompt()

    full_response = ""
    pending_text = ""
    last_response_id = previous_response_id or ""
    first_chunk_state = {"is_first": True}

    flush_index = 0
    flush_end_time: float | None = None

    async def _flush(text_to_speak: str) -> None:
        nonlocal first_byte_logged, flush_index, flush_end_time
        if not text_to_speak.strip():
            return
        gap_ms = round((time.monotonic() - flush_end_time) * 1000, 1) if flush_end_time else None
        if gap_ms is not None and gap_ms > 50:
            log.warning(
                "tts_inter_flush_gap_large",
                session_id=str(session_id),
                sequence=sequence,
                flush_index=flush_index,
                inter_flush_gap_ms=gap_ms,
                detail="gap between sentence flushes > 50 ms — ElevenLabs silence padding may cause audible double-silence or pop",
            )
        log.info(
            "tts_flush_start",
            session_id=str(session_id),
            sequence=sequence,
            flush_index=flush_index,
            chars=len(text_to_speak),
            text_preview=text_to_speak[:60],
            inter_flush_gap_ms=gap_ms,
        )
        flush_index += 1
        t_flush = time.monotonic()
        frames_sent, total_pcm_bytes = await _send_audio_chunks(
            websocket,
            stream_tts_pcm(text_to_speak, history_queue),
            first_chunk_state,
        )
        flush_end_time = time.monotonic()
        log.info(
            "tts_flush_done",
            session_id=str(session_id),
            sequence=sequence,
            flush_index=flush_index - 1,
            duration_ms=round((flush_end_time - t_flush) * 1000, 1),
            frames_sent=frames_sent,
            total_pcm_bytes=total_pcm_bytes,
            is_total_pcm_aligned=total_pcm_bytes % 2 == 0,
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
    except asyncio.CancelledError:
        # Disconnect-driven cancellation: stream aclose runs as the async-for
        # iterators unwind, which terminates the upstream OpenAI/ElevenLabs
        # HTTP requests. Re-raise so the TaskGroup unwinds cleanly.
        log.info(
            "turn_cancelled_on_disconnect",
            session_id=str(session_id),
            sequence=sequence,
        )
        raise
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
    """
    Reader/processor split inside a TaskGroup so a client disconnect cancels
    any in-flight pipeline turn rather than waiting for the next send_bytes
    to fail. Without this, OpenAI/ElevenLabs streams keep consuming tokens
    after the user has hung up.
    """
    await websocket.accept()
    log.info("ws_connected", session_id=str(session_id))

    inbox: asyncio.Queue[str | None] = asyncio.Queue()
    max_bytes = settings.max_ws_text_frame_bytes

    async def _reader() -> None:
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
                await inbox.put(raw)
        finally:
            # Sentinel wakes the processor if it's blocked on inbox.get().
            await inbox.put(None)

    async def _processor() -> None:
        previous_response_id: str | None = None
        sequence = 0
        while True:
            raw = await inbox.get()
            if raw is None:
                return
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

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_reader(), name="ws-reader")
            tg.create_task(_processor(), name="ws-processor")
    except* WebSocketDisconnect:
        log.info("ws_disconnected", session_id=str(session_id))
    except* Exception as eg:
        for exc in eg.exceptions:
            log.error("ws_unhandled_error", session_id=str(session_id), error=str(exc))
