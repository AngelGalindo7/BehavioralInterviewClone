"""Unit tests for the async circuit breaker."""
import asyncio

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.exceptions import CircuitOpenError


@pytest.mark.asyncio
async def test_starts_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_successful_call_stays_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)

    async def _succeed():
        return 42

    result = await cb.call(_succeed)
    assert result == 42
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_opens_after_threshold_failures():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)

    async def _fail():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(_fail)

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_raises_circuit_open_error():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    with pytest.raises(CircuitOpenError):
        await cb.call(_fail)


@pytest.mark.asyncio
async def test_recovers_after_timeout(monkeypatch):
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    async def _succeed():
        return "ok"

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    assert cb.state == CircuitState.OPEN

    # Wait for recovery window
    await asyncio.sleep(0.02)

    result = await cb.call(_succeed)
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_state_label():
    cb = CircuitBreaker("test")
    assert cb.state_label() == "closed"


# ── check() ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_does_not_raise_when_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
    await cb.check()  # must not raise


@pytest.mark.asyncio
async def test_check_raises_circuit_open_when_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60.0)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    with pytest.raises(CircuitOpenError):
        await cb.check()


@pytest.mark.asyncio
async def test_check_transitions_to_half_open_after_recovery():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    await asyncio.sleep(0.02)
    await cb.check()  # should not raise and should transition
    assert cb.state == CircuitState.HALF_OPEN


# ── on_failure() / on_success() ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_failure_increments_count_and_opens_at_threshold():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
    exc = RuntimeError("err")

    await cb.on_failure(exc)
    assert cb.state == CircuitState.CLOSED  # threshold not yet reached

    await cb.on_failure(exc)
    assert cb.state == CircuitState.OPEN  # threshold reached


@pytest.mark.asyncio
async def test_on_success_closes_circuit_and_resets_count():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    await asyncio.sleep(0.02)
    await cb.check()  # → HALF_OPEN
    await cb.on_success()
    assert cb.state == CircuitState.CLOSED


# ── HALF_OPEN transitions ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_half_open_closes_on_successful_call():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    async def _succeed():
        return "ok"

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    await asyncio.sleep(0.02)
    result = await cb.call(_succeed)  # probe call in HALF_OPEN
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_reopens_on_failed_call():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    await asyncio.sleep(0.02)
    # Probe call fails — circuit should re-open
    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_on_failure_via_public_method_reopens_half_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)

    async def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(_fail)

    await asyncio.sleep(0.02)
    await cb.check()  # → HALF_OPEN
    await cb.on_failure(RuntimeError("another"))
    assert cb.state == CircuitState.OPEN
