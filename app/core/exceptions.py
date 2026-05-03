class BehavioralDummyError(Exception):
    pass


class CircuitOpenError(BehavioralDummyError):
    """Raised when a circuit breaker is in OPEN state."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Circuit '{name}' is OPEN — rejecting call fast")
        self.circuit_name = name


class EmbeddingError(BehavioralDummyError):
    pass


class TTSError(BehavioralDummyError):
    pass


class SimliError(BehavioralDummyError):
    pass
