from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# probes=10 on lists=100 scans 10% of the IVFFlat index.
# At ~10k anecdotes this takes ~100ms on the t4g.small — acceptable within the
# 630ms TTFB budget. Raise probes for better recall at the cost of latency.
_PROBES = 10


async def retrieve_anecdotes(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int | None = None,
) -> list[str]:
    """
    Return the top-k most similar anecdote contents via IVFFlat cosine search.
    """
    k = top_k if top_k is not None else settings.rag_top_k

    await session.execute(text(f"SET ivfflat.probes = {_PROBES}"))
    result = await session.execute(
        text(
            "SELECT content "
            "FROM anecdotes "
            "ORDER BY embedding <=> :embedding "
            "LIMIT :k"
        ),
        {"embedding": str(query_embedding), "k": k},
    )
    return [row.content for row in result]
