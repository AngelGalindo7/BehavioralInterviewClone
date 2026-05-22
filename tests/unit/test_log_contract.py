"""
Pin the structlog event names the Grafana dashboards query by literal string.

Renaming any event below silently breaks
observability/grafana/dashboards/behavioral_dummy.json. This is a static
text scan, not a runtime check — the goal is a cheap CI tripwire on rename.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

LOG_CONTRACT: list[tuple[str, str]] = [
    ("app/core/lifespan.py", "app_startup_complete"),
    ("app/core/lifespan.py", "app_shutdown_complete"),
    ("app/api/ws_interview.py", "ws_connected"),
    ("app/api/ws_interview.py", "ws_disconnected"),
    ("app/api/ws_interview.py", "turn_stage_timing"),
    ("app/api/ws_interview.py", "client_stage_timing"),
    ("app/audio/tts.py", "tts_history_deleted"),
    ("app/core/circuit_breaker.py", "circuit_opened"),
    ("app/core/circuit_breaker.py", "circuit_half_open"),
    ("app/core/circuit_breaker.py", "circuit_closed"),
]

# Structured-field names referenced by the latency-waterfall panel in
# behavioral_dummy.json. Each one becomes an `unwrap <field>` in a Loki query,
# so a rename here silently empties the panel until the dashboard is updated.
TIMING_FIELDS: list[tuple[str, str]] = [
    ("app/api/ws_interview.py", "llm_ttft_ms"),
    ("app/api/ws_interview.py", "llm_total_ms"),
    ("app/api/ws_interview.py", "tts_first_chunk_ms"),
    ("app/api/ws_interview.py", "tts_total_ms"),
]


@pytest.mark.parametrize(("relpath", "event"), LOG_CONTRACT)
def test_dashboard_event_name_present(relpath: str, event: str) -> None:
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert f'"{event}"' in source, (
        f"Grafana dashboard event '{event}' missing from {relpath}; "
        f"a rename here will silently break behavioral_dummy.json panels."
    )


def test_ttfb_ms_field_present_in_ws_interview() -> None:
    relpath = "app/api/ws_interview.py"
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "ttfb_ms=" in source, (
        f"Structured field 'ttfb_ms=' missing from {relpath}; "
        f"the TTFB latency panel in behavioral_dummy.json depends on it."
    )


@pytest.mark.parametrize(("relpath", "field"), TIMING_FIELDS)
def test_dashboard_timing_field_present(relpath: str, field: str) -> None:
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert f'"{field}"' in source, (
        f"Structured field '{field}' missing from {relpath}; "
        f"the per-stage waterfall panel in behavioral_dummy.json depends on it."
    )
