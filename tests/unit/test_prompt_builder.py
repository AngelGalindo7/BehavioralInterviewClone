"""Unit tests for the RAG prompt builder."""
from app.rag.prompt_builder import build_system_prompt


def test_anecdotes_appear_in_prompt():
    anecdotes = ["I led a team of five.", "I reduced latency by 40%."]
    prompt = build_system_prompt(anecdotes, candidate_name="Alex")
    assert "I led a team of five." in prompt
    assert "I reduced latency by 40%." in prompt


def test_candidate_name_appears():
    prompt = build_system_prompt(["any anecdote"], candidate_name="Jordan")
    assert "Jordan" in prompt


def test_numbered_anecdotes():
    anecdotes = ["first", "second", "third"]
    prompt = build_system_prompt(anecdotes, candidate_name="Test")
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "[3]" in prompt


def test_empty_anecdotes_still_returns_prompt():
    prompt = build_system_prompt([], candidate_name="Test")
    assert isinstance(prompt, str)
    assert len(prompt) > 0
