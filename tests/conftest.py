import os
from unittest.mock import AsyncMock, patch

import pytest

# Set required env vars before any app module is imported so Pydantic Settings
# validation doesn't fail during test collection.
# Tests on Windows can't build asyncpg (no C compiler). aiosqlite is a stand-in
# for engine-construction time; tests that hit the DB mock it at the session layer.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ELEVENLABS_API_KEY", "el-test")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "voice-test")
os.environ.setdefault("SIMLI_API_KEY", "simli-test")
os.environ.setdefault("SIMLI_FACE_ID", "face-test")


@pytest.fixture
def lifespan_mocks():
    """Patch both startup DB checks so TestClient can run without a live database.

    Must be active for the *entire* test body — including the moment
    ``TestClient(app).__enter__()`` triggers the lifespan — so this is a
    fixture rather than an inline context manager.
    """
    with (
        patch("app.core.lifespan._verify_db_connection", new_callable=AsyncMock),
        patch("app.core.lifespan._warmup_ivfflat_index", new_callable=AsyncMock),
    ):
        yield
