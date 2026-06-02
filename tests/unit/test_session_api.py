"""Unit tests for POST /session/ and DELETE /session/{id}."""
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.deps import get_db
from app.main import create_app


# ── Shared helpers ────────────────────────────────────────────────────────────

def _db_override(mock_session):
    """FastAPI dependency override that yields *mock_session*."""
    async def _inner() -> AsyncGenerator:
        yield mock_session
    return _inner


def _found_db(fake_row=None, ended_at=None):
    """Mock DB session where SELECT returns a row and UPDATE succeeds.

    *ended_at* defaults to None so close_session_if_active treats the row as
    active and proceeds to UPDATE. Pass a datetime to simulate an already-ended
    session (the idempotent path).
    """
    db = AsyncMock()
    if fake_row is None:
        fake_row = MagicMock()
        fake_row.ended_at = ended_at
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fake_row
    db.execute.side_effect = [select_result, MagicMock()]
    return db


def _not_found_db():
    """Mock DB session where SELECT returns no row."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    return db


# ── POST /session/ ────────────────────────────────────────────────────────────

def test_create_session_returns_201(lifespan_mocks, auth_cookies):
    fake_id = uuid.uuid4()
    mock_db = AsyncMock()

    async def _set_id(obj):
        obj.id = fake_id

    mock_db.refresh.side_effect = _set_id

    app = create_app()
    app.dependency_overrides[get_db] = _db_override(mock_db)
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.post("/session/")

    assert resp.status_code == 201


def test_create_session_response_contains_session_id(lifespan_mocks, auth_cookies):
    fake_id = uuid.uuid4()
    mock_db = AsyncMock()

    async def _set_id(obj):
        obj.id = fake_id

    mock_db.refresh.side_effect = _set_id

    app = create_app()
    app.dependency_overrides[get_db] = _db_override(mock_db)
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.post("/session/")

    assert resp.json()["session_id"] == str(fake_id)


def test_create_session_commits_and_refreshes(lifespan_mocks, auth_cookies):
    fake_id = uuid.uuid4()
    mock_db = AsyncMock()

    async def _set_id(obj):
        obj.id = fake_id

    mock_db.refresh.side_effect = _set_id

    app = create_app()
    app.dependency_overrides[get_db] = _db_override(mock_db)
    with TestClient(app, cookies=auth_cookies) as client:
        client.post("/session/")

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()


# ── DELETE /session/{id} ──────────────────────────────────────────────────────

def test_end_session_returns_200(lifespan_mocks, auth_cookies):
    app = create_app()
    app.dependency_overrides[get_db] = _db_override(_found_db())
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.delete(f"/session/{uuid.uuid4()}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ended"}


def test_end_session_issues_update_with_ended_at(lifespan_mocks, auth_cookies):
    mock_db = _found_db()
    app = create_app()
    app.dependency_overrides[get_db] = _db_override(mock_db)
    with TestClient(app, cookies=auth_cookies) as client:
        client.delete(f"/session/{uuid.uuid4()}")

    assert mock_db.execute.call_count == 2
    update_sql = str(mock_db.execute.call_args_list[1][0][0])
    assert "ended_at" in update_sql.lower()


def test_end_session_unknown_id_returns_404(lifespan_mocks, auth_cookies):
    app = create_app()
    app.dependency_overrides[get_db] = _db_override(_not_found_db())
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.delete(f"/session/{uuid.uuid4()}")

    assert resp.status_code == 404


def test_end_session_invalid_uuid_returns_422(lifespan_mocks, auth_cookies):
    app = create_app()
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.delete("/session/not-a-uuid")

    assert resp.status_code == 422


def test_end_session_already_ended_is_idempotent(lifespan_mocks, auth_cookies):
    """Repeat DELETE (e.g. pagehide + WS finally race) must not overwrite the
    first ended_at timestamp — close_session_if_active short-circuits."""
    from datetime import datetime, timezone

    mock_db = _found_db(ended_at=datetime.now(timezone.utc).replace(tzinfo=None))
    app = create_app()
    app.dependency_overrides[get_db] = _db_override(mock_db)
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.delete(f"/session/{uuid.uuid4()}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ended"}
    # SELECT ran, but no UPDATE — only one execute call.
    assert mock_db.execute.call_count == 1
    mock_db.commit.assert_not_called()
