"""
Sentence-aware text chunker for personal anecdotes.

Strategy:
  1. Split on double newline (paragraph boundaries) first.
  2. If a paragraph exceeds max_tokens, further split on sentence boundaries
     via regex (no NLTK dependency — avoids ARM install issues).
  3. Apply a sliding overlap of overlap_tokens between adjacent chunks so
     context is not lost at boundaries.

Token count is approximated as len(text) // 4 (GPT tokeniser average).
"""
import re
from pathlib import Path

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_APPROX_CHARS_PER_TOKEN = 4


def _approx_tokens(text: str) -> int:
    return len(text) // _APPROX_CHARS_PER_TOKEN


def _split_sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(paragraph) if s.strip()]


def chunk_text(
    text: str,
    max_tokens: int = 300,
    overlap_tokens: int = 30,
) -> list[str]:
    """Return a list of text chunks from *text*."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: list[str] = []
    for para in paragraphs:
        if _approx_tokens(para) <= max_tokens:
            sentences.append(para)
        else:
            sentences.extend(_split_sentences(para))

    chunks: list[str] = []
    current_sentences: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _approx_tokens(sent)
        if current_tokens + sent_tokens > max_tokens and current_sentences:
            chunks.append(" ".join(current_sentences))
            # Retain overlap: walk back from end until overlap_tokens exhausted
            overlap: list[str] = []
            overlap_count = 0
            for s in reversed(current_sentences):
                t = _approx_tokens(s)
                if overlap_count + t > overlap_tokens:
                    break
                overlap.insert(0, s)
                overlap_count += t
            current_sentences = overlap
            current_tokens = overlap_count

        current_sentences.append(sent)
        current_tokens += sent_tokens

    if current_sentences:
        chunks.append(" ".join(current_sentences))

    return chunks


def load_and_chunk(directory: str) -> list[tuple[str, str]]:
    """
    Read all .txt and .md files from *directory*, chunk each, and return
    a flat list of (chunk_text, source_filename) tuples.
    """
    results: list[tuple[str, str]] = []
    for path in sorted(Path(directory).glob("**/*")):
        if path.suffix not in {".txt", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for chunk in chunk_text(text):
            results.append((chunk, path.name))
    return results
