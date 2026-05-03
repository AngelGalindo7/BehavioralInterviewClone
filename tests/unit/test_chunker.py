"""Unit tests for the PCM audio chunker."""
from app.audio.chunker import iter_pcm_chunks


def test_chunks_are_at_most_chunk_size():
    data = bytes(range(256)) * 100  # 25600 bytes
    chunks = list(iter_pcm_chunks(data, chunk_size=6000))
    assert all(len(c) <= 6000 for c in chunks)


def test_chunks_reconstruct_original():
    data = bytes(range(256)) * 47  # 12032 bytes — not divisible by 6000
    chunks = list(iter_pcm_chunks(data, chunk_size=6000))
    assert b"".join(chunks) == data


def test_empty_input_yields_nothing():
    chunks = list(iter_pcm_chunks(b"", chunk_size=6000))
    assert chunks == []


def test_single_chunk_when_smaller_than_size():
    data = b"\x00" * 1000
    chunks = list(iter_pcm_chunks(data, chunk_size=6000))
    assert len(chunks) == 1
    assert chunks[0] == data


def test_exact_multiple_produces_no_partial():
    data = b"\xff" * 12000
    chunks = list(iter_pcm_chunks(data, chunk_size=6000))
    assert len(chunks) == 2
    assert all(len(c) == 6000 for c in chunks)


def test_chunk_size_one_yields_individual_bytes():
    data = b"\x01\x02\x03"
    chunks = list(iter_pcm_chunks(data, chunk_size=1))
    assert len(chunks) == 3
    assert chunks == [b"\x01", b"\x02", b"\x03"]


def test_chunk_size_larger_than_data_yields_one_chunk():
    data = b"\xAA\xBB"
    chunks = list(iter_pcm_chunks(data, chunk_size=10_000))
    assert len(chunks) == 1
    assert chunks[0] == data


def test_last_chunk_is_smaller_when_not_divisible():
    data = bytes(7)  # 7 bytes, chunk_size=4 → [4, 3]
    chunks = list(iter_pcm_chunks(data, chunk_size=4))
    assert len(chunks) == 2
    assert len(chunks[0]) == 4
    assert len(chunks[1]) == 3


def test_chunk_content_integrity_with_non_zero_data():
    data = bytes(range(256))
    chunks = list(iter_pcm_chunks(data, chunk_size=100))
    assert b"".join(chunks) == data
    assert chunks[0] == bytes(range(100))
    assert chunks[1] == bytes(range(100, 200))
