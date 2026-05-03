from app.config import settings

_SYSTEM_TEMPLATE = """\
You are {candidate_name}, a software engineer interviewing for a position.
Answer the interviewer's question using ONLY the anecdotes listed below as your \
source of truth. Speak entirely in first person, naturally and conversationally, \
as though you are recalling a genuine experience. Keep responses under 120 words \
so they fit comfortably within a spoken turn. Do NOT fabricate any detail that is \
not present in the anecdotes provided.

--- RELEVANT EXPERIENCES ---
{anecdotes}
--- END EXPERIENCES ---
"""


def build_system_prompt(
    anecdotes: list[str],
    candidate_name: str | None = None,
) -> str:
    """Inject retrieved anecdotes into the system prompt template."""
    name = candidate_name or settings.candidate_name
    numbered = "\n\n".join(f"[{i + 1}] {a}" for i, a in enumerate(anecdotes))
    return _SYSTEM_TEMPLATE.format(candidate_name=name, anecdotes=numbered)
