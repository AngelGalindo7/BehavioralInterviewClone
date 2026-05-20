from abc import ABC, abstractmethod
from typing import ClassVar, Literal

AvatarMode = Literal["audio_pcm", "text", "audio_pcm_server"]


class AvatarSessionProvider(ABC):
    """
    Base contract for an avatar session.

    Three integration modes drive the WS pipeline branch in ws_interview.py:
      - "audio_pcm": backend streams ElevenLabs PCM through /ws/interview and
        the frontend SDK consumes it (Simli). `speak()` and `send_pcm()` are
        never called.
      - "text": backend POSTs text directly to the provider, which runs TTS
        and lip-sync server-side (legacy HeyGen v1 streaming, deprecated).
        PCM never flows over any WS for this provider.
      - "audio_pcm_server": backend opens a server-to-provider WebSocket and
        forwards ElevenLabs PCM as base64 events (LiveAvatar LITE). PCM does
        not cross the browser WS; the frontend only consumes a LiveKit room.

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

    async def send_pcm(self, avatar_session_id: str, pcm: bytes, *, is_first: bool) -> None:
        """
        Forward a chunk of PCM16 audio to the provider's server-side WebSocket.
        Only meaningful when mode == "audio_pcm_server". `is_first` lets the
        provider mark utterance start (e.g. open a new agent.speak sequence).
        """
        raise NotImplementedError(f"{type(self).__name__}.send_pcm() not implemented")

    async def send_pcm_end(self, avatar_session_id: str) -> None:
        """
        Signal end-of-utterance for audio_pcm_server providers so the avatar
        finalises lip-sync for the current speak() sequence. No-op default for
        non-pcm-server providers.
        """
        return None

    async def close(self, avatar_session_id: str) -> None:
        """Tear down a server-side session by id. Best-effort; default is no-op."""
        return None
