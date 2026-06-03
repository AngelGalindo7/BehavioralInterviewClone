"""
WebSocket /ws/interview — the real-time interview orchestration core.

Protocol (client → server, JSON text frames, capped at settings.max_ws_text_frame_bytes):
  {"type": "transcript", "text": "<interviewer question>"}
  {"type": "greeting"}  — play the fixed opener once (TTS only, no LLM); sent by
                       the client when the avatar is ready. Backend guards
                       against replay and does not count it against max_turns.
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
import contextlib
import json
import random
import re
import time
import uuid
from collections.abc import AsyncIterator

import structlog
from elevenlabs import VoiceSettings
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.api.session import close_session_if_active
from app.audio.chunker import iter_pcm_chunks
from app.audio.tts import stream_tts_pcm
from app.avatar.base import AvatarSessionProvider
from app.avatar.protocol import AUDIO_IMMEDIATE_PREFIX, AUDIO_NORMAL_PREFIX
from app.avatar.providers.heygen import LIVEAVATAR_TTS_OUTPUT_FORMAT
from app.config import settings
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.db.engine import AsyncSessionLocal
from app.db.models import Turn
from app.deps import (
    get_avatar_provider,
    get_avatar_provider_by_name,
    get_elevenlabs_cb,
    get_history_queue,
    get_openai_cb,
)
from app.llm.responder import generate_response
from app.rag.prompt_builder import build_system_prompt

# Custom WS close codes for client-visible end-of-session reasons.
# 4000-4999 are reserved for application use per RFC 6455.
WS_CLOSE_MAX_TURNS = 4002
WS_CLOSE_MAX_DURATION = 4003

# RAG — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# from app.rag.embedder import embed_text
# from app.rag.retriever import retrieve_anecdotes

log = structlog.get_logger()
router = APIRouter()

_FALLBACK_PCM = bytes(settings.pcm_chunk_bytes)

# Fixed opener spoken once per session when the client signals the avatar is
# ready. Routed straight through TTS (no LLM) so the wording is exact and the
# turn costs zero OpenAI tokens.
GREETING_TEXT = (
    "Hey there! I'm Angel's behavioral clone — "
    "feel free to ask me any behavioral questions!"
)

# Upbeat delivery for the opener only (lower stability, higher style). Interview
# answers keep the calmer global profile; see config.elevenlabs_greeting_*.
_GREETING_VOICE_SETTINGS = VoiceSettings(
    stability=settings.elevenlabs_greeting_stability,
    similarity_boost=settings.elevenlabs_similarity_boost,
    style=settings.elevenlabs_greeting_style,
    use_speaker_boost=settings.elevenlabs_use_speaker_boost,
)

# Sentence-ending punctuation followed by whitespace (or EOL). The lookahead
# avoids matching decimals like "3.14"; "e.g. " false-positives are tolerable.
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s)|[.!?]$")
# Eager boundary used only on the FIRST flush of a turn. Adds clause-level
# punctuation (comma, semicolon, colon) so a short opener like "Sure, " can
# fire TTS immediately instead of waiting for a full sentence.
_FIRST_FLUSH_BOUNDARY = re.compile(r"[.!?,;:](?=\s)|[.!?,;:]$")
_TERMINAL_PUNCT = frozenset(".!?,;:")


def _split_at_boundary(text: str, max_chars: int) -> tuple[str, str]:
    """
    Return (to_flush, remaining). Prefer the latest sentence boundary so each
    flush gives ElevenLabs more text per call (better prosody, fewer requests).
    Force-flush at the last word boundary before max_chars to avoid cutting
    mid-word; falls back to the whole buffer only if no space exists.
    """
    last_match = None
    for m in _SENTENCE_BOUNDARY.finditer(text):
        last_match = m
    if last_match is not None:
        end = last_match.end()
        return text[:end], text[end:].lstrip()
    if len(text) >= max_chars:
        split_pos = text.rfind(" ", 0, max_chars)
        if split_pos > 0:
            return text[:split_pos], text[split_pos:].lstrip()
        return text, ""
    return "", text


def _split_at_first_flush_boundary(text: str, min_chars: int) -> tuple[str, str]:
    """
    First-flush variant: return (to_flush, remaining) only once *text* has at
    least *min_chars* characters AND a clause/sentence boundary has been seen.
    Cuts at the LATEST boundary in range so the flush carries as much text as
    possible without exceeding it. Returns ("", text) until both conditions hold.
    """
    if len(text) < min_chars:
        return "", text
    last_match = None
    for m in _FIRST_FLUSH_BOUNDARY.finditer(text):
        last_match = m
    if last_match is None:
        return "", text
    end = last_match.end()
    return text[:end], text[end:].lstrip()


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

    ElevenLabs frequently returns odd-length raw chunks. Slicing each chunk
    independently would send Simli a frame ending on half a PCM16 sample, which
    it decodes as a noise burst (static). The carry byte bridges chunk boundaries
    so every WebSocket frame contains only complete 2-byte samples.
    """
    frames_sent = 0
    total_pcm_bytes = 0
    misaligned_frames = 0
    raw_chunk_index = 0
    # Holds the orphaned first byte of a PCM16 sample when ElevenLabs emits an
    # odd-length chunk; prepended to the next chunk before slicing.
    carry = b""

    async for tts_bytes in pcm_iter:
        raw_len = len(tts_bytes)
        log.debug(
            "ws_tts_raw_chunk_received",
            raw_chunk_index=raw_chunk_index,
            raw_bytes=raw_len,
            is_pcm16_aligned=raw_len % 2 == 0,
        )
        raw_chunk_index += 1

        buf = carry + tts_bytes
        aligned_len = len(buf) & ~1  # round down to nearest even byte count
        carry = buf[aligned_len:]    # 0 or 1 byte carried to next iteration
        if aligned_len == 0:
            continue

        for pcm_chunk in iter_pcm_chunks(buf[:aligned_len], settings.pcm_chunk_bytes):
            pcm_len = len(pcm_chunk)
            is_immediate = first_chunk_state["is_first"]
            if is_immediate:
                payload = AUDIO_IMMEDIATE_PREFIX + pcm_chunk
                first_chunk_state["is_first"] = False
            else:
                payload = AUDIO_NORMAL_PREFIX + pcm_chunk

            if pcm_len % 2 != 0:
                misaligned_frames += 1
                log.warning(
                    "ws_send_frame_misaligned",
                    frame_index=frames_sent,
                    pcm_bytes=pcm_len,
                    is_immediate=is_immediate,
                    detail="odd-length frame despite carry buffer — logic error",
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

    # ElevenLabs total is always even (confirmed by logs) but pad defensively.
    # A zero byte = digital silence for the orphaned half-sample.
    if carry:
        log.info(
            "ws_carry_byte_padded",
            detail="ElevenLabs stream ended on odd byte; padded final half-sample with silence",
        )
        padded = carry + b"\x00"
        for pcm_chunk in iter_pcm_chunks(padded, settings.pcm_chunk_bytes):
            pcm_len = len(pcm_chunk)
            is_immediate = first_chunk_state["is_first"]
            payload = (AUDIO_IMMEDIATE_PREFIX if is_immediate else AUDIO_NORMAL_PREFIX) + pcm_chunk
            if is_immediate:
                first_chunk_state["is_first"] = False
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


async def _speak_greeting(
    websocket: WebSocket,
    history_queue: asyncio.Queue[str],
    provider: AvatarSessionProvider,
    avatar_session_id: str | None,
) -> None:
    """
    Speak GREETING_TEXT through the same per-provider audio path a normal turn
    uses, but with no LLM in the loop. Mirrors the provider branch in _flush:
    text-mode speaks the string directly; audio_pcm_server forwards PCM to the
    provider socket; audio_pcm (Simli) streams PCM to the browser WS with the
    1-byte dispatch prefix. Best-effort — a greeting failure must not abort the
    session, so the caller swallows exceptions.
    """
    if provider.mode == "text":
        assert avatar_session_id is not None  # validated at WS handshake
        await provider.speak(avatar_session_id, GREETING_TEXT)
        return

    if provider.mode == "audio_pcm_server":
        assert avatar_session_id is not None  # validated at WS handshake
        is_first = True
        async for tts_bytes in stream_tts_pcm(
            GREETING_TEXT, history_queue, output_format=LIVEAVATAR_TTS_OUTPUT_FORMAT,
            voice_settings=_GREETING_VOICE_SETTINGS,
        ):
            if not tts_bytes:
                continue
            await provider.send_pcm(avatar_session_id, tts_bytes, is_first=is_first)
            is_first = False
        await provider.send_pcm_end(avatar_session_id)
        return

    first_chunk_state = {"is_first": True}
    await _send_audio_chunks(
        websocket,
        stream_tts_pcm(
            GREETING_TEXT, history_queue, voice_settings=_GREETING_VOICE_SETTINGS
        ),
        first_chunk_state,
    )


async def _handle_transcript(
    websocket: WebSocket,
    question: str,
    session_id: uuid.UUID,
    sequence: int,
    history: list[dict[str, str]],
    openai_cb: CircuitBreaker,
    elevenlabs_cb: CircuitBreaker,
    history_queue: asyncio.Queue[str],
    provider: AvatarSessionProvider,
    avatar_session_id: str | None,
    turn_id: str | None = None,
) -> int:
    """
    Run the full pipeline for one interviewer question.
    next_sequence only advances if the turn was successfully persisted, so
    the turns table never has gaps caused by mid-pipeline failures.

    Cancellation: when the enclosing task is cancelled (WS disconnect), the
    CancelledError propagates through the OpenAI and ElevenLabs async-for loops,
    closing their underlying httpx responses. tts.py's finally block enqueues
    any captured history_item_id before re-raising, so even a cancelled stream
    cleans up its ElevenLabs history row.

    Provider branch:
      - text (legacy): provider.speak() per flush, no ElevenLabs.
      - audio_pcm (Simli): stream ElevenLabs PCM @ 16 kHz through the browser WS.
      - audio_pcm_server (LiveAvatar LITE): stream ElevenLabs PCM @ 24 kHz to
        provider.send_pcm() (which forwards over the provider's server-side
        WebSocket as base64). Frontend WS sees no audio for this provider.
    """
    is_text_mode = provider.mode == "text"
    is_pcm_server_mode = provider.mode == "audio_pcm_server"
    uses_elevenlabs = not is_text_mode
    tts_output_format = LIVEAVATAR_TTS_OUTPUT_FORMAT if is_pcm_server_mode else None
    t0 = time.monotonic()
    first_byte_logged = False

    # Per-stage waterfall timestamps for the blog-post latency budget.
    # Captured in the hot path and emitted as a single turn_stage_timing log
    # at the end of the turn (including the error paths). Simli does not expose
    # a server-visible first-frame ack — frontend rVFC fills that gap and ships
    # a client_timing message keyed by turn_id.
    llm_first_token_t: float | None = None
    llm_total_t: float | None = None
    first_flush_start_t: float | None = None
    last_flush_end_t: float | None = None
    tts_first_chunk_t: float | None = None

    def _log_stage_timing(outcome: str) -> None:
        stages: dict[str, float] = {}
        if llm_first_token_t is not None:
            stages["llm_ttft_ms"] = round((llm_first_token_t - t0) * 1000, 1)
        if llm_total_t is not None:
            stages["llm_total_ms"] = round((llm_total_t - t0) * 1000, 1)
        if tts_first_chunk_t is not None and first_flush_start_t is not None:
            stages["tts_first_chunk_ms"] = round(
                (tts_first_chunk_t - first_flush_start_t) * 1000, 1
            )
        if first_flush_start_t is not None and last_flush_end_t is not None:
            stages["tts_total_ms"] = round(
                (last_flush_end_t - first_flush_start_t) * 1000, 1
            )
        log.info(
            "turn_stage_timing",
            session_id=str(session_id),
            sequence=sequence,
            turn_id=turn_id,
            outcome=outcome,
            mode=provider.mode,
            **stages,
        )

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
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": question},
    ]

    full_response = ""
    pending_text = ""
    first_chunk_state = {"is_first": True}

    flush_index = 0
    flush_end_time: float | None = None
    previous_tts_text: str | None = None
    tts_seed = random.randint(0, 999_999_999)

    async def _timed_tts_stream(
        text_to_speak: str,
    ) -> AsyncIterator[bytes]:
        """
        Wrap stream_tts_pcm and stamp tts_first_chunk_t on the first PCM byte
        the upstream ElevenLabs stream yields for this turn. Subsequent flushes
        leave the timestamp untouched so it always reflects the very first
        chunk the user could possibly have heard.
        """
        nonlocal tts_first_chunk_t
        async for chunk in stream_tts_pcm(
            text_to_speak, history_queue, output_format=tts_output_format,
            previous_text=previous_tts_text, seed=tts_seed,
        ):
            if tts_first_chunk_t is None:
                tts_first_chunk_t = time.monotonic()
            yield chunk

    async def _flush(text_to_speak: str) -> None:
        nonlocal first_byte_logged, flush_index, flush_end_time
        nonlocal first_flush_start_t, last_flush_end_t, previous_tts_text
        if not text_to_speak.strip():
            return
        # Force-flushed mid-sentence text has no terminal punctuation; ElevenLabs
        # generates artifact phonemes when an utterance ends on a bare word.
        stripped = text_to_speak.rstrip()
        if stripped and stripped[-1] not in _TERMINAL_PUNCT:
            text_to_speak = stripped + ","
        # CB check moved here from the pre-LLM position. Lets OpenAI streaming
        # begin without waiting on the breaker lock, and means each flush is
        # independently breaker-aware rather than only the first. Skipped for
        # text-mode providers since ElevenLabs is bypassed entirely.
        if uses_elevenlabs:
            await elevenlabs_cb.check()
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
            mode=provider.mode,
        )
        flush_index += 1
        t_flush = time.monotonic()
        if first_flush_start_t is None:
            first_flush_start_t = t_flush
        if is_text_mode:
            assert avatar_session_id is not None  # validated at WS handshake
            await provider.speak(avatar_session_id, text_to_speak)
            flush_end_time = time.monotonic()
            last_flush_end_t = flush_end_time
            log.info(
                "avatar_text_flush_done",
                session_id=str(session_id),
                sequence=sequence,
                flush_index=flush_index - 1,
                duration_ms=round((flush_end_time - t_flush) * 1000, 1),
            )
        elif is_pcm_server_mode:
            assert avatar_session_id is not None  # validated at WS handshake
            total_pcm_bytes = 0
            chunks_sent = 0
            is_first_chunk = first_chunk_state["is_first"]
            async for tts_bytes in _timed_tts_stream(text_to_speak):
                if not tts_bytes:
                    continue
                await provider.send_pcm(
                    avatar_session_id, tts_bytes, is_first=is_first_chunk
                )
                is_first_chunk = False
                first_chunk_state["is_first"] = False
                chunks_sent += 1
                total_pcm_bytes += len(tts_bytes)
            await provider.send_pcm_end(avatar_session_id)
            flush_end_time = time.monotonic()
            last_flush_end_t = flush_end_time
            log.info(
                "avatar_pcm_server_flush_done",
                session_id=str(session_id),
                sequence=sequence,
                flush_index=flush_index - 1,
                duration_ms=round((flush_end_time - t_flush) * 1000, 1),
                chunks_sent=chunks_sent,
                total_pcm_bytes=total_pcm_bytes,
            )
        else:
            frames_sent, total_pcm_bytes = await _send_audio_chunks(
                websocket,
                _timed_tts_stream(text_to_speak),
                first_chunk_state,
            )
            flush_end_time = time.monotonic()
            last_flush_end_t = flush_end_time
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
        previous_tts_text = text_to_speak
        if not first_byte_logged:
            first_byte_logged = True
            log.info(
                "ttfb_first_byte",
                session_id=str(session_id),
                sequence=sequence,
                ttfb_ms=round((time.monotonic() - t0) * 1000, 1),
                mode=provider.mode,
            )

    # Decouple LLM token consumption from TTS blocking. Previously,
    # `await _flush(sentence)` stalled the OpenAI iterator for the full
    # duration of each ElevenLabs call (~200-400ms + stream time), so
    # sentence N+1's tokens weren't consumed until N finished playing.
    # With a sentence queue, _llm_to_queue runs freely while _flush_consumer
    # streams audio — by the time sentence N finishes, N+1's text is already
    # queued and ElevenLabs starts immediately, cutting inter-sentence gaps
    # from (token_wait + EL_latency) down to just EL_latency (~200ms).
    _sentence_q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)

    async def _llm_to_queue() -> None:
        nonlocal full_response, pending_text
        nonlocal llm_first_token_t, llm_total_t
        first_sentence_queued = False
        try:
            async for delta in generate_response(messages, openai_cb):
                if llm_first_token_t is None:
                    llm_first_token_t = time.monotonic()
                full_response += delta
                pending_text += delta
                if first_sentence_queued:
                    to_flush, pending_text = _split_at_boundary(
                        pending_text, settings.sentence_boundary_max_chars
                    )
                else:
                    to_flush, pending_text = _split_at_first_flush_boundary(
                        pending_text, settings.first_flush_min_chars
                    )
                if to_flush:
                    first_sentence_queued = True
                    await _sentence_q.put(to_flush)
            llm_total_t = time.monotonic()
            if pending_text.strip():
                await _sentence_q.put(pending_text.strip())
        finally:
            await _sentence_q.put(None)

    async def _flush_consumer() -> None:
        while True:
            text = await _sentence_q.get()
            if text is None:
                return
            await _flush(text)

    try:
        await asyncio.gather(_llm_to_queue(), _flush_consumer())
        if uses_elevenlabs:
            await elevenlabs_cb.on_success()
    except asyncio.CancelledError:
        # Cancellation (client disconnect, duration cap, or a barge-in skip):
        # stream aclose runs as the async-for iterators unwind, which terminates
        # the upstream OpenAI/ElevenLabs HTTP requests and stops their billing
        # for the remainder of this turn. Re-raise so the caller unwinds cleanly.
        log.info(
            "turn_cancelled",
            session_id=str(session_id),
            sequence=sequence,
        )
        _log_stage_timing("cancelled")
        raise
    except CircuitOpenError:
        log.error("circuit_open_during_turn", session_id=str(session_id))
        if provider.mode == "audio_pcm":
            await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Service temporarily unavailable. Please try again."})
            )
        except Exception:
            pass
        _log_stage_timing("circuit_open")
        return sequence
    except Exception as exc:
        log.error("turn_pipeline_error", session_id=str(session_id), error=str(exc))
        if uses_elevenlabs:
            await elevenlabs_cb.on_failure(exc)
        if provider.mode == "audio_pcm":
            await websocket.send_bytes(AUDIO_IMMEDIATE_PREFIX + _FALLBACK_PCM)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Something went wrong. Please try again."})
            )
        except Exception:
            pass
        _log_stage_timing("error")
        return sequence

    if not full_response.strip():
        _log_stage_timing("empty_response")
        return sequence

    ttfb_ms = (time.monotonic() - t0) * 1000
    log.info(
        "turn_complete",
        session_id=str(session_id),
        sequence=sequence,
        ttfb_ms=round(ttfb_ms, 1),
        question_preview=question[:60],
    )
    _log_stage_timing("ok")

    persisted = await _persist_turn(
        session_id=session_id,
        sequence=sequence,
        question=question,
        response=full_response,
        response_id=None,
        ttfb_ms=ttfb_ms,
    )
    # Append only on a successful, non-empty turn — a cancelled (barge-in) or
    # errored turn returns earlier without appending, so the abandoned answer
    # never enters conversation memory. Independent of DB persist so a transient
    # write failure doesn't drop the running context.
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": full_response})
    next_sequence = sequence + 1 if persisted else sequence
    return next_sequence


@router.websocket("/ws/interview")
async def interview_ws(
    websocket: WebSocket,
    session_id: uuid.UUID,
    provider_name: str | None = Query(default=None, alias="provider"),
    avatar_session_id: str | None = Query(default=None),
    openai_cb: CircuitBreaker = Depends(get_openai_cb),
    elevenlabs_cb: CircuitBreaker = Depends(get_elevenlabs_cb),
    history_queue: asyncio.Queue[str] = Depends(get_history_queue),
    default_provider: AvatarSessionProvider = Depends(get_avatar_provider),
) -> None:
    """
    Reader/processor split inside a TaskGroup so a client disconnect cancels
    any in-flight pipeline turn rather than waiting for the next send_bytes
    to fail. Without this, OpenAI/ElevenLabs streams keep consuming tokens
    after the user has hung up.

    The avatar provider is resolved per-WS from the ?provider= query param,
    falling back to the DI default. Text-mode providers (HeyGen) also require
    ?avatar_session_id= so the backend can route speak()/close() to the
    correct upstream session. We refuse the WS at handshake time when this
    invariant doesn't hold, so handlers never see a half-configured provider.
    """
    if provider_name is None:
        provider = default_provider
    else:
        resolved = get_avatar_provider_by_name(provider_name)
        if resolved is None:
            await websocket.close(code=4400, reason=f"unknown provider: {provider_name!r}")
            return
        provider = resolved

    if provider.mode in ("text", "audio_pcm_server") and not avatar_session_id:
        await websocket.close(
            code=4400,
            reason=f"avatar_session_id required for {provider.mode} providers",
        )
        return

    await websocket.accept()
    log.info(
        "ws_connected",
        session_id=str(session_id),
        provider=type(provider).__name__,
        mode=provider.mode,
        avatar_session_id=avatar_session_id,
    )

    inbox: asyncio.Queue[str | None] = asyncio.Queue()
    max_bytes = settings.max_ws_text_frame_bytes
    # Handle to the turn the processor is currently running, so the reader can
    # cancel it on a barge-in skip. Single-writer (processor) / single-reader
    # (this reader); the asyncio single-threaded model makes the dict access
    # safe without a lock.
    current_turn: dict[str, asyncio.Task | None] = {"task": None}

    def _is_skip(raw: str) -> bool:
        # Substring pre-check keeps us from json-parsing every (potentially 4 KB)
        # transcript frame twice — only frames that could be a skip get parsed.
        if '"skip"' not in raw:
            return False
        try:
            return json.loads(raw).get("type") == "skip"
        except (json.JSONDecodeError, AttributeError):
            return False

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
                # Barge-in is a control signal: cancel the in-flight turn
                # directly instead of queuing the skip behind it in inbox, where
                # it wouldn't be read until that turn — and all of its billed
                # ElevenLabs synthesis — had already completed.
                if _is_skip(raw):
                    task = current_turn["task"]
                    if task is not None and not task.done():
                        log.info("ws_skip_cancelling_turn", session_id=str(session_id))
                        task.cancel()
                    else:
                        log.debug(
                            "ws_skip_noop_no_active_turn", session_id=str(session_id)
                        )
                    continue
                await inbox.put(raw)
        finally:
            # Sentinel wakes the processor if it's blocked on inbox.get().
            await inbox.put(None)

    async def _processor() -> None:
        history: list[dict[str, str]] = []
        sequence = 0
        turns_accepted = 0
        greeted = False
        max_turns = settings.max_turns_per_session
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
                # Normally intercepted out-of-band in _reader so it can cancel
                # the in-flight turn immediately; reaching here means no turn was
                # running when it was sent. Nothing to do.
                log.debug("ws_skip_no_active_turn", session_id=str(session_id))
                continue
            if msg_type == "greeting":
                # Deterministic opener — fired by the client when the avatar is
                # ready. Once-only guard so a reconnect resend can't replay it.
                # Not counted against max_turns (no LLM, no billing).
                if not greeted:
                    greeted = True
                    log.info("ws_greeting", session_id=str(session_id))
                    try:
                        await _speak_greeting(
                            websocket, history_queue, provider, avatar_session_id
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning(
                            "ws_greeting_failed",
                            session_id=str(session_id),
                            error=str(exc),
                        )
                continue
            if msg_type == "client_timing":
                # Frontend per-event waterfall events keyed by the turn_id the
                # client sent on the transcript. Forwarded straight to Loki so
                # the Grafana panel can correlate browser-side and backend
                # stages by turn_id without a custom join layer.
                raw_events = msg.get("events") or {}
                stage_fields = {
                    k: v for k, v in raw_events.items()
                    if isinstance(v, (int, float))
                }
                log.info(
                    "client_stage_timing",
                    session_id=str(session_id),
                    turn_id=msg.get("turn_id"),
                    **stage_fields,
                )
                continue
            if msg_type != "transcript":
                log.debug("ws_unknown_message_type", msg_type=msg_type)
                continue

            question = (msg.get("text") or "").strip()
            if not question:
                continue
            client_turn_id = msg.get("turn_id") if isinstance(msg.get("turn_id"), str) else None

            # Per-session turn cap. Counted on accepted transcripts only —
            # skips and malformed frames don't bill upstream APIs.
            if turns_accepted >= max_turns:
                log.warning(
                    "ws_max_turns_reached",
                    session_id=str(session_id),
                    turns_accepted=turns_accepted,
                    max_turns=max_turns,
                )
                await websocket.close(
                    code=WS_CLOSE_MAX_TURNS,
                    reason="session turn limit reached",
                )
                return
            turns_accepted += 1

            # Run the turn as a child task so _reader can cancel it on a barge-in
            # skip. Cancelling propagates CancelledError through the OpenAI and
            # ElevenLabs async-for loops, closing their httpx responses and
            # stopping upstream token/character billing for audio the user has
            # already chosen not to hear.
            turn_task = asyncio.create_task(
                _handle_transcript(
                    websocket=websocket,
                    question=question,
                    session_id=session_id,
                    sequence=sequence,
                    history=history,
                    openai_cb=openai_cb,
                    elevenlabs_cb=elevenlabs_cb,
                    history_queue=history_queue,
                    provider=provider,
                    avatar_session_id=avatar_session_id,
                    turn_id=client_turn_id,
                ),
                name="ws-turn",
            )
            current_turn["task"] = turn_task
            try:
                sequence = await turn_task
            except asyncio.CancelledError:
                self_task = asyncio.current_task()
                # Distinguish a barge-in (the reader cancelled *turn_task*, we are
                # not) from our own teardown (disconnect / duration cap cancels
                # the processor). On 3.11+ a pending cancellation of the awaiting
                # task surfaces via cancelling(); a clean barge-in leaves it at 0.
                turn_cancelled_by_skip = turn_task.cancelled() and (
                    self_task is None or self_task.cancelling() == 0
                )
                if not turn_cancelled_by_skip:
                    # Processor is being torn down. Make sure the orphaned turn
                    # task can't keep streaming — and billing — after we unwind.
                    if not turn_task.done():
                        turn_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await turn_task
                    current_turn["task"] = None
                    raise
                # Barge-in: sequence stays put (the cancelled turn never
                # persisted) and history is unchanged (the cancelled turn never
                # appended), so the abandoned answer never enters the conversation.
                log.info(
                    "turn_cancelled_on_skip",
                    session_id=str(session_id),
                    sequence=sequence,
                )
                # Server-rendered avatars (LiveAvatar) play from a buffer the
                # browser can't clear; tell the provider to drop the rest of the
                # utterance so it stops billing for the skipped audio. No-op for
                # Simli (browser cleared its own buffer). Done after the turn task
                # has fully unwound so no send_pcm races this interrupt frame.
                if provider.mode == "audio_pcm_server" and avatar_session_id:
                    with contextlib.suppress(Exception):
                        await provider.interrupt(avatar_session_id)
            finally:
                # Only clear if this invocation still owns the slot — the
                # disconnect branch above already cleared and re-raised.
                if current_turn["task"] is turn_task:
                    current_turn["task"] = None

    async def _duration_watchdog() -> None:
        """
        Hard ceiling on a single WS session — matches simli_max_session_length
        so the upstream avatar timeout and the backend timeout coincide.
        Closing the WS cancels the reader and processor (and any in-flight
        OpenAI/ElevenLabs stream) via the TaskGroup, same as a client disconnect.
        """
        await asyncio.sleep(settings.session_max_age_seconds)
        log.info(
            "ws_max_duration_reached",
            session_id=str(session_id),
            max_age_seconds=settings.session_max_age_seconds,
        )
        await websocket.close(
            code=WS_CLOSE_MAX_DURATION,
            reason="session duration limit reached",
        )

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_reader(), name="ws-reader")
            tg.create_task(_processor(), name="ws-processor")
            tg.create_task(_duration_watchdog(), name="ws-duration-watchdog")
    except* WebSocketDisconnect:
        log.info("ws_disconnected", session_id=str(session_id))
    except* Exception as eg:
        for exc in eg.exceptions:
            log.error("ws_unhandled_error", session_id=str(session_id), error=str(exc))
    finally:
        # Both text-mode (legacy HeyGen) and audio_pcm_server (LiveAvatar)
        # providers hold a server-side session that must be torn down explicitly;
        # otherwise the upstream avatar session leaks until its idle timeout.
        # close() is best-effort: it logs and swallows on failure so a flaky
        # provider can't keep this handler hanging.
        if provider.mode in ("text", "audio_pcm_server") and avatar_session_id:
            await provider.close(avatar_session_id)
        # Defense-in-depth DB close. Frontend pagehide DELETE may race with us
        # or fail entirely (browser killed, fetch keepalive dropped); the
        # close-on-disconnect here means the WS is the authoritative end-of-session
        # signal. Idempotent on the SELECT inside close_session_if_active.
        try:
            async with AsyncSessionLocal() as db:
                outcome = await close_session_if_active(db, session_id)
            if outcome == "ended":
                log.info("session_closed_on_ws_finally", session_id=str(session_id))
        except Exception as exc:
            log.warning(
                "session_close_on_ws_finally_failed",
                session_id=str(session_id),
                error=str(exc),
            )
