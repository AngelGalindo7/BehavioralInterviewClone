"""
Audio frame prefix protocol for backend → browser → avatar SDK.

The simli-client v3.x SDK exposes two methods for ingesting raw PCM:
  - sendAudioDataImmediate(): bypass the SDK's internal jitter buffer and play
    the chunk as soon as it arrives. Use for the FIRST chunk of each utterance
    so lip-sync starts immediately.
  - sendAudioData(): standard buffered ingest. Use for all subsequent chunks.

The backend prepends one byte to every PCM frame it sends over /ws/interview:
  0x01 → frontend dispatches to sendAudioDataImmediate (first chunk per utterance)
  0x00 → frontend dispatches to sendAudioData (subsequent chunks)

Anything else is treated as 0x00 by the frontend (defensive).
"""

AUDIO_IMMEDIATE_PREFIX: bytes = b"\x01"
AUDIO_NORMAL_PREFIX: bytes = b"\x00"
