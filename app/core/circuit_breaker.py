import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

import structlog

from app.core.exceptions import CircuitOpenError

log = structlog.get_logger()
T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Async circuit breaker with three states.

    CLOSED  → normal operation
    OPEN    → fast-fail for recovery_timeout seconds after failure_threshold failures
    HALF_OPEN → lets one probe request through; recovers or re-opens
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float = 0.0
        # Admits exactly one probe through call() while HALF_OPEN; concurrent
        # callers fast-fail so a recovery window sends a single request at a
        # possibly-still-down upstream instead of a thundering herd.
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def state_label(self) -> str:
        return self._state.value

    async def check(self) -> None:
        """Fast-fail check: raise CircuitOpenError if open, transition to HALF_OPEN if recovered."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed < self._recovery_timeout:
                    raise CircuitOpenError(self.name)
                log.info("circuit_half_open", circuit=self.name)
                self._state = CircuitState.HALF_OPEN

    async def on_success(self) -> None:
        await self._on_success()

    async def on_failure(self, exc: Exception) -> None:
        await self._on_failure(exc)

    async def call(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed < self._recovery_timeout:
                    raise CircuitOpenError(self.name)
                log.info("circuit_half_open", circuit=self.name)
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = True
            elif self._state == CircuitState.HALF_OPEN:
                # A probe is already validating recovery — reject the rest so we
                # don't stampede an upstream that may still be down. on_success/
                # on_failure (always reached below) releases the slot.
                if self._probe_in_flight:
                    raise CircuitOpenError(self.name)
                self._probe_in_flight = True

        try:
            result = await coro_factory()
        except Exception as exc:
            await self._on_failure(exc)
            raise

        await self._on_success()
        return result

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state != CircuitState.CLOSED:
                log.info("circuit_closed", circuit=self.name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._probe_in_flight = False

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._probe_in_flight = False
            log.warning(
                "circuit_failure",
                circuit=self.name,
                count=self._failure_count,
                threshold=self._failure_threshold,
                error=str(exc),
            )
            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.error("circuit_opened", circuit=self.name)
