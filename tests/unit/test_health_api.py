"""Unit tests for GET /health and GET /ready endpoints."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_returns_200(lifespan_mocks):
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


def test_health_returns_ok_body(lifespan_mocks):
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.json() == {"status": "ok"}


# ── /ready ────────────────────────────────────────────────────────────────────

def test_ready_returns_200_when_db_reachable(lifespan_mocks):
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.api.health.engine") as mock_engine:
        mock_engine.connect.return_value = mock_ctx
        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/ready")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_ready_executes_select_against_db(lifespan_mocks):
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.api.health.engine") as mock_engine:
        mock_engine.connect.return_value = mock_ctx
        app = create_app()
        with TestClient(app) as client:
            client.get("/ready")

    mock_conn.execute.assert_called_once()
    executed_sql = str(mock_conn.execute.call_args[0][0])
    assert "SELECT" in executed_sql.upper()


def test_ready_returns_500_when_db_unavailable(lifespan_mocks):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.api.health.engine") as mock_engine:
        mock_engine.connect.return_value = mock_ctx
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/ready")

    assert resp.status_code == 500
