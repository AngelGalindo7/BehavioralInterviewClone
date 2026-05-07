from collections.abc import Iterator

import structlog

log = structlog.get_logger(__name__)


def iter_pcm_chunks(pcm_bytes: bytes, chunk_size: int = 6000) -> Iterator[bytes]:
    """
    Yield fixed-size slices of raw PCM16 LE audio.

    6000 bytes = 1500 samples at 16 kHz = 93.75 ms of audio.
    Simli's WebRTC ingest expects chunks at approximately this cadence.
    The final slice may be smaller; Simli handles partial chunks.
    """
    total = len(pcm_bytes)
    if total % 2 != 0:
        log.warning(
            "chunker_input_misaligned",
            input_bytes=total,
            detail="odd-byte input to iter_pcm_chunks — PCM16 sample boundary broken; last byte is half a sample",
        )

    chunk_index = 0
    for i in range(0, total, chunk_size):
        chunk = pcm_bytes[i : i + chunk_size]
        chunk_len = len(chunk)
        if chunk_len % 2 != 0:
            log.warning(
                "chunker_output_chunk_misaligned",
                chunk_index=chunk_index,
                chunk_bytes=chunk_len,
                input_bytes=total,
                detail="odd-length output chunk sent to Simli — likely produces a pop/click",
            )
        else:
            log.debug(
                "chunker_output_chunk",
                chunk_index=chunk_index,
                chunk_bytes=chunk_len,
            )
        chunk_index += 1
        yield chunk
