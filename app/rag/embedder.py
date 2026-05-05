from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed_text(text: str) -> list[float]:
    """Return an embedding vector for *text*. Asserts dim matches schema."""
    response = await _get_client().embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
    )
    embedding = response.data[0].embedding
    expected = settings.openai_embedding_dimensions
    if len(embedding) != expected:
        raise ValueError(
            f"Embedding dimension mismatch: model {settings.openai_embedding_model} "
            f"returned {len(embedding)} dims; schema requires {expected}"
        )
    return embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed *texts*. Returns a list of vectors in the same order."""
    if not texts:
        return []

    response = await _get_client().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )

    embeddings: list[list[float]] = []
    expected = settings.openai_embedding_dimensions

    for item in response.data:
        embedding = item.embedding
        if len(embedding) != expected:
            raise ValueError(
                f"Embedding dimension mismatch: model {settings.openai_embedding_model} "
                f"returned {len(embedding)} dims; schema requires {expected}"
            )
        embeddings.append(embedding)

    return embeddings
