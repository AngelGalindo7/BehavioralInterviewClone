"""Unit tests for the prompt builder (non-RAG path)."""
from app.rag.prompt_builder import build_system_prompt, set_stories_cache


def test_candidate_name_appears():
    prompt = build_system_prompt(candidate_name="Jordan")
    assert "Jordan" in prompt


def test_returns_string():
    prompt = build_system_prompt(candidate_name="Test")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_stories_injected_into_prompt():
    set_stories_cache("I shipped a thing.")
    prompt = build_system_prompt(candidate_name="Alex")
    assert "I shipped a thing." in prompt


def test_empty_stories_still_returns_prompt():
    set_stories_cache("")
    prompt = build_system_prompt(candidate_name="Test")
    assert isinstance(prompt, str)
    assert len(prompt) > 0
