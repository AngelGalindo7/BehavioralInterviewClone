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
