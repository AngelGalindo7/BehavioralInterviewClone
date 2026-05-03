"""Batch embedding via OpenAI text-embedding-3-small."""
from openai import AsyncOpenAI

_MODEL = "text-embedding-3-small"
_DIMENSIONS = 1536

_client: AsyncOpenAI | None = None


def _get_client(api_key: str | None) -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=api_key)
    return _client


async def batch_embed(
    chunks: list[tuple[str, str]],
    batch_size: int = 100,
    api_key: str | None = None,
) -> list[list[float]]:
    """
    Embed *chunks* in batches. Returns a list of 1536-dim float vectors in
    the same order as the input chunks. Asserts every returned vector matches
    the schema dimension before returning.
    """
    client = _get_client(api_key)
    texts = [content for content, _ in chunks]
    embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = await client.embeddings.create(model=_MODEL, input=batch)
        for item in response.data:
            if len(item.embedding) != _DIMENSIONS:
                raise ValueError(
                    f"Embedding dim mismatch: model {_MODEL} returned "
                    f"{len(item.embedding)}; expected {_DIMENSIONS}"
                )
            embeddings.append(item.embedding)
        print(f"Embedded {min(i + batch_size, len(texts))}/{len(texts)} chunks")

    return embeddings
