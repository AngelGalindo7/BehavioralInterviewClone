"""Unit tests for the WS→LiveAvatar real-time pacing helpers."""
import asyncio

import pytest

from app.api.ws_interview import (
    _drain_and_pace,
    _pacing_delay_s,
    _pcm_bytes_per_sec,
)
from app.core.exceptions import TTSError


def test_pcm_bytes_per_sec_parses_format():
    assert _pcm_bytes_per_sec("pcm_24000") == 48000  # 24 kHz * 2 bytes
    assert _pcm_bytes_per_sec("pcm_16000") == 32000


def test_pcm_bytes_per_sec_falls_back_on_garbage():
    assert _pcm_bytes_per_sec("mp3") == 48000
    assert _pcm_bytes_per_sec("pcm_notanumber") == 48000


def test_pacing_no_delay_until_past_lead():
    # 0.5 s of audio sent, 0 s elapsed, 0.5 s lead → exactly at the lead, no sleep
    assert _pacing_delay_s(24000, 48000, 0.0, 0.5) == 0.0


def test_pacing_sleeps_by_how_far_ahead_beyond_lead():
    # 1.0 s of audio sent, 0.1 s elapsed, 0.2 s lead → sleep 1.0 - 0.1 - 0.2
    assert _pacing_delay_s(48000, 48000, 0.1, 0.2) == pytest.approx(0.7)


def test_pacing_never_negative_when_behind():
    # only 0.1 s of audio sent but 5 s elapsed → we're behind, never sleep
    assert _pacing_delay_s(4800, 48000, 5.0, 0.5) == 0.0


def test_pacing_guards_zero_rate():
    assert _pacing_delay_s(48000, 0, 0.0, 0.5) == 0.0


# _drain_and_pace: the decoupled reader/pacer. lead_s=1e9 forces _pacing_delay_s
# to 0 so the pacing sleep never fires — isolating the read/send decoupling from
# wall-clock timing so these tests stay fast and deterministic.


async def test_drain_reads_upstream_fully_even_while_send_blocks():
    """Regression for the mid-answer cut-off: a slow avatar send must NOT throttle
    the ElevenLabs read. With every downstream send gated, the reader still drains
    the entire upstream source — proving pacing no longer back-pressures the
    stream-input WS (the old behaviour that made ElevenLabs drop the turn)."""
    consumed = 0
    fully_read = asyncio.Event()

    async def source():
        nonlocal consumed
        for _ in range(10):
            consumed += 1
            yield bytes(6000)
        fully_read.set()

    gate = asyncio.Event()
    sent: list[int] = []

    async def send(piece, is_first):
        await gate.wait()  # block every send until the test releases it
        sent.append(len(piece))

    task = asyncio.create_task(
        _drain_and_pace(source(), send, chunk_size=6000, bytes_per_sec=48000, lead_s=1e9)
    )
    # Upstream is fully consumed even though not one send has completed.
    await asyncio.wait_for(fully_read.wait(), timeout=1.0)
    assert consumed == 10
    assert sent == []  # sender is gated — nothing delivered yet

    gate.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert sent == [6000] * 10  # all pieces delivered, in order, after release


async def test_drain_rechunks_and_preserves_order():
    """A single large upstream chunk is split into chunk_size pieces and delivered
    in order — the reader/pacer split drops and reorders nothing."""
    sent: list[int] = []

    async def source():
        yield bytes(15000)  # 6000 + 6000 + 3000

    async def send(piece, is_first):
        sent.append(len(piece))

    await _drain_and_pace(source(), send, chunk_size=6000, bytes_per_sec=48000, lead_s=1e9)
    assert sent == [6000, 6000, 3000]


async def test_drain_marks_only_first_send_is_first():
    """is_first is True for exactly the first piece of the turn, False thereafter."""
    firsts: list[bool] = []

    async def source():
        yield bytes(12000)

    async def send(piece, is_first):
        firsts.append(is_first)

    await _drain_and_pace(source(), send, chunk_size=6000, bytes_per_sec=48000, lead_s=1e9)
    assert firsts == [True, False]


async def test_drain_propagates_reader_error():
    """A TTSError raised by the ElevenLabs stream (error frame) surfaces to the
    caller after the queue drains, so the circuit breaker records the failure —
    and audio produced before the error is still delivered."""
    sent: list[int] = []

    async def source():
        yield bytes(6000)
        raise TTSError("elevenlabs error frame")

    async def send(piece, is_first):
        sent.append(len(piece))

    with pytest.raises(TTSError):
        await _drain_and_pace(
            source(), send, chunk_size=6000, bytes_per_sec=48000, lead_s=1e9
        )
    assert sent == [6000]  # the pre-error chunk was still delivered


async def test_drain_cancel_stops_the_reader_no_leak():
    """On cancellation (barge-in / client disconnect) the helper must cancel its
    reader task — no orphan left reading (and billing) ElevenLabs after the turn
    is abandoned. Verified by proving the upstream source stops being pulled once
    the cancelled helper has unwound."""
    pulled = 0
    gate = asyncio.Event()

    async def source():
        nonlocal pulled
        while True:
            pulled += 1
            yield bytes(6000)
            await asyncio.sleep(0.005)  # bound the read rate so the queue stays small

    async def send(piece, is_first):
        await gate.wait()  # never delivers — keeps the turn 'in flight'

    task = asyncio.create_task(
        _drain_and_pace(source(), send, chunk_size=6000, bytes_per_sec=48000, lead_s=0.0)
    )
    await asyncio.sleep(0.05)  # reader races ahead while send is gated
    assert pulled > 0

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task  # helper unwinds: its finally cancels + awaits the reader
    settled = pulled

    await asyncio.sleep(0.05)  # a leaked reader would keep pulling during this window
    assert pulled == settled, "reader task kept consuming the source after cancellation"


async def test_drain_preserves_order_under_real_pacing_sleeps():
    """With pacing actually sleeping (lead_s=0 forces a delay after the first
    piece), pieces still arrive in order — the read/pace split holds under the
    real timing path, not just the lead_s=1e9 fast path."""
    sent: list[int] = []

    async def source():
        for _ in range(6):
            yield bytes(6000)

    async def send(piece, is_first):
        sent.append(len(piece))

    # bytes_per_sec=480000 keeps each piece's pacing delay ~12.5 ms so the test
    # stays fast while still exercising the asyncio.sleep branch.
    await _drain_and_pace(source(), send, chunk_size=6000, bytes_per_sec=480000, lead_s=0.0)
    assert sent == [6000] * 6
