"""Unit tests for the WS→LiveAvatar real-time pacing helpers."""
import pytest

from app.api.ws_interview import _pacing_delay_s, _pcm_bytes_per_sec


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
