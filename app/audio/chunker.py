from collections.abc import Iterator


def iter_pcm_chunks(pcm_bytes: bytes, chunk_size: int = 6000) -> Iterator[bytes]:
    """
    Yield fixed-size slices of raw PCM16 LE audio.

    6000 bytes = 1500 samples at 16 kHz = 93.75 ms of audio.
    Simli's WebRTC ingest expects chunks at approximately this cadence.
    The final slice may be smaller; Simli handles partial chunks.
    """
    for i in range(0, len(pcm_bytes), chunk_size):
        yield pcm_bytes[i : i + chunk_size]
