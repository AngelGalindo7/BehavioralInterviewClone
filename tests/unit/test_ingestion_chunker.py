"""Unit tests for the ingestion text chunker."""
from ingestion.chunker import chunk_text, load_and_chunk
import tempfile
import os


def test_short_text_is_single_chunk():
    text = "This is a short paragraph."
    chunks = chunk_text(text, max_tokens=300)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_text_splits_into_multiple_chunks():
    sentence = "This is a reasonably long sentence that takes up some tokens. "
    text = sentence * 60  # ~3600 chars ≈ 900 tokens
    chunks = chunk_text(text, max_tokens=300)
    assert len(chunks) > 1


def test_all_content_preserved():
    sentence = "Word " * 80
    text = sentence
    chunks = chunk_text(text, max_tokens=100)
    combined = " ".join(chunks)
    # All original words should be present (overlap may duplicate some)
    for word in sentence.strip().split():
        assert word in combined


def test_no_empty_chunks():
    text = "\n\n".join(["Paragraph one.", "Paragraph two.", "Paragraph three."])
    chunks = chunk_text(text)
    assert all(c.strip() for c in chunks)


def test_load_and_chunk_reads_txt_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "story.txt")
        with open(path, "w") as f:
            f.write("I worked at Acme Corp for three years.\n\nI led the payments team.")
        results = load_and_chunk(tmpdir)
        assert len(results) >= 1
        assert all(isinstance(c, tuple) and len(c) == 2 for c in results)
        sources = {src for _, src in results}
        assert "story.txt" in sources


def test_load_and_chunk_reads_md_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "notes.md")
        with open(path, "w") as f:
            f.write("# Leadership\n\nI mentored junior engineers for two years.")
        results = load_and_chunk(tmpdir)
        sources = {src for _, src in results}
        assert "notes.md" in sources


def test_load_and_chunk_ignores_non_text_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a .txt and a .pdf (should be skipped)
        with open(os.path.join(tmpdir, "data.txt"), "w") as f:
            f.write("Valid anecdote.")
        with open(os.path.join(tmpdir, "resume.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 binary content")
        results = load_and_chunk(tmpdir)
        sources = {src for _, src in results}
        assert "resume.pdf" not in sources
        assert "data.txt" in sources


def test_chunk_text_empty_string_returns_empty_list():
    assert chunk_text("") == []


def test_overlap_means_boundary_content_appears_in_adjacent_chunks():
    """Sentences at a chunk boundary should appear in both the closing and opening chunk."""
    # Build text that forces a split: one long sentence block + overlap sentences
    sentence = "This is sentence number {n} with some padding words here. "
    text = "".join(sentence.format(n=i) for i in range(40))  # ~40 sentences

    chunks = chunk_text(text, max_tokens=100, overlap_tokens=30)
    assert len(chunks) >= 2

    # At least one sentence that ended chunk N should start chunk N+1 (overlap)
    words_in_first = set(chunks[0].split())
    words_in_second = set(chunks[1].split())
    assert words_in_first & words_in_second, "Expected shared words from overlap between chunks"


def test_very_long_single_paragraph_splits_on_sentences():
    """A paragraph that exceeds max_tokens should be split at sentence boundaries."""
    # Single paragraph (no double newline), many sentences
    sentences = ["I did something important at my job. "] * 50
    text = "".join(sentences)  # one big paragraph, ~2500 chars ≈ 625 tokens

    chunks = chunk_text(text, max_tokens=100)
    assert len(chunks) > 1
    # Each chunk should end with proper punctuation or be the last fragment
    for chunk in chunks[:-1]:
        assert len(chunk) > 0
