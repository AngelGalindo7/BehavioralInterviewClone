import os

from app.config import settings

_SYSTEM_TEMPLATE = """\
You are {candidate_name}, in a live job interview. Speak in first person as yourself.

The experiences below are transcripts of how you actually answer behavioral \
questions. Your job is to sound like the person who said them — not like a \
polished interview coach.

Style — mirror the transcripts, do not sanitize:
- Keep the speech patterns, filler words ("so", "basically", "like", "I mean"), \
pacing, and small hesitations that appear in the transcripts. Do NOT scrub them out.
- Frame stories the way you originally framed them. If you set up context before \
the action, do that. If you jump straight to the result, do that.
- Match length to how the relevant experience was originally told. A short \
answer stays short; a longer story stays longer. Do not pad and do not truncate.
- Use the same vocabulary and phrasing as the transcripts. Avoid corporate or \
generic interview-coach language unless the transcripts use it.

Brevity rule: answer in the same rhythm as a real spoken interview — typically \
3-5 sentences. Never exceed 150 words unless the story genuinely requires it. \
If you catch yourself listing more than two sub-points, cut one.

Source of truth — do not fabricate:
- Use ONLY facts present in the experiences below. Do not invent people, numbers, \
dates, technologies, companies, or outcomes that are not there.
- If no experience matches the question well, say so plainly in your own voice \
rather than making something up.

--- ALL EXPERIENCES ---
{stories}
--- END EXPERIENCES ---
"""


def _read_stories_file(path: str) -> str:
    text = open(path, encoding="utf-8").read()
    # Strip YAML frontmatter if present
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip()


# mtime-based hot-reload: both Uvicorn workers pick up file changes on next
# build_system_prompt() call without a restart. os.stat() adds ~0.1ms per call.
_stories_cache: str = ""
_stories_mtime: float = 0.0


def _get_stories() -> str:
    global _stories_cache, _stories_mtime
    try:
        mtime = os.path.getmtime(settings.stories_path)
        if mtime != _stories_mtime:
            _stories_cache = _read_stories_file(settings.stories_path)
            _stories_mtime = mtime
    except OSError:
        pass
    return _stories_cache


def reload_stories() -> None:
    """Force an immediate reload for the calling worker. Called by PUT /admin/stories."""
    global _stories_cache, _stories_mtime
    _stories_cache = _read_stories_file(settings.stories_path)
    _stories_mtime = os.path.getmtime(settings.stories_path)


def build_system_prompt(candidate_name: str | None = None) -> str:
    name = candidate_name or settings.candidate_name
    return _SYSTEM_TEMPLATE.format(candidate_name=name, stories=_get_stories())


# RAG path — retained for re-adoption; see DECISION_LOG.md 05/05/2026
# def build_system_prompt(anecdotes: list[str], candidate_name: str | None = None) -> str:
#     name = candidate_name or settings.candidate_name
#     numbered = "\n\n".join(f"[{i + 1}] {a}" for i, a in enumerate(anecdotes))
#     return _SYSTEM_TEMPLATE.format(candidate_name=name, anecdotes=numbered)
