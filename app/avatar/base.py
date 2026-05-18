from abc import ABC, abstractmethod
from typing import ClassVar, Literal

AvatarMode = Literal["audio_pcm", "text"]


class AvatarSessionProvider(ABC):
    """
    Base contract for an avatar session.

    Two integration modes drive the WS pipeline branch in ws_interview.py:
      - "audio_pcm": backend streams ElevenLabs PCM through /ws/interview and
        the frontend SDK consumes it (Simli). `speak()` is never called.
      - "text": backend POSTs text directly to the provider, which runs TTS
        and lip-sync server-side (HeyGen). PCM never flows over the WS for
        this provider's sessions.

    Subclasses MUST declare `mode` as a class attribute.
    """

    mode: ClassVar[AvatarMode]

    @abstractmethod
    async def get_session(self) -> dict:
        """Return session credentials for the frontend SDK to join."""

    async def speak(self, avatar_session_id: str, text: str) -> None:
        """
        Send text to the provider for server-side TTS + lip-sync.
        Only meaningful when mode == "text"; callers must branch on mode first.
        """
        raise NotImplementedError(f"{type(self).__name__}.speak() not implemented")

    async def close(self, avatar_session_id: str) -> None:
        """Tear down a server-side session by id. Best-effort; default is no-op."""
        return None
