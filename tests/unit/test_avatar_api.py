"""Unit tests for POST /avatar/session."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.avatar.providers.simli import SimliSessionProvider
from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.deps import get_avatar_provider
from app.main import create_app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_httpx_mock(token_json: dict, ice_json: list, token_status: int = 200, ice_status: int = 200):
    """
    Return a mock httpx.AsyncClient context whose .post() yields token_json and
    whose .get() yields ice_json, mirroring the v3 /compose/token + /compose/ice flow.
    """
    def _resp(json_data, status_code):
        r = MagicMock()
        if status_code >= 400:
            r.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=status_code)
            )
        else:
            r.raise_for_status.return_value = None
        r.json.return_value = json_data
        return r

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_resp(token_json, token_status))
    mock_client.get = AsyncMock(return_value=_resp(ice_json, ice_status))

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx, mock_client


def _open_circuit_breaker() -> CircuitBreaker:
    cb = CircuitBreaker("avatar", failure_threshold=1, recovery_timeout=3600.0)
    cb._state = CircuitState.OPEN
    cb._opened_at = time.monotonic()
    return cb


# ── Happy path ────────────────────────────────────────────────────────────────

def test_avatar_session_returns_200(lifespan_mocks, auth_cookies):
    http_ctx, _ = _make_httpx_mock({"session_token": "tok-xyz"}, [{"urls": ["stun:stun.l.google.com:19302"]}])
    with patch("app.avatar.providers.simli.httpx.AsyncClient", return_value=http_ctx):
        app = create_app()
        with TestClient(app, cookies=auth_cookies) as client:
            resp = client.post("/avatar/session")

    assert resp.status_code == 200


def test_avatar_session_returns_session_token_and_ice_servers(lifespan_mocks, auth_cookies):
    ice = [{"urls": ["turn:example.com:3478"], "username": "u", "credential": "p"}]
    http_ctx, _ = _make_httpx_mock({"session_token": "tok-abc"}, ice)
    with patch("app.avatar.providers.simli.httpx.AsyncClient", return_value=http_ctx):
        app = create_app()
        with TestClient(app, cookies=auth_cookies) as client:
            resp = client.post("/avatar/session")

    body = resp.json()
    assert body["session_token"] == "tok-abc"
    assert body["ice_servers"] == ice


def test_avatar_session_posts_v3_required_fields(lifespan_mocks, auth_cookies):
    http_ctx, mock_client = _make_httpx_mock({"session_token": "t"}, [])
    with patch("app.avatar.providers.simli.httpx.AsyncClient", return_value=http_ctx):
        app = create_app()
        with TestClient(app, cookies=auth_cookies) as client:
            client.post("/avatar/session")

    sent_json = mock_client.post.call_args.kwargs["json"]
    sent_headers = mock_client.post.call_args.kwargs["headers"]
    assert sent_json.get("faceId")
    assert sent_json.get("handleSilence") is True
    assert isinstance(sent_json.get("maxSessionLength"), int)
    assert isinstance(sent_json.get("maxIdleTime"), int)
    assert sent_json.get("model") in {"fasttalk", "artalk"}
    assert sent_headers.get("x-simli-api-key")


def test_avatar_session_502_when_response_missing_session_token(lifespan_mocks, auth_cookies):
    http_ctx, _ = _make_httpx_mock({}, [])
    with patch("app.avatar.providers.simli.httpx.AsyncClient", return_value=http_ctx):
        app = create_app()
        with TestClient(app, cookies=auth_cookies, raise_server_exceptions=False) as client:
            resp = client.post("/avatar/session")

    assert resp.status_code == 502


# ── Circuit breaker open ──────────────────────────────────────────────────────

def test_avatar_session_returns_503_when_circuit_open(lifespan_mocks, auth_cookies):
    open_cb = _open_circuit_breaker()
    provider = SimliSessionProvider(open_cb)
    app = create_app()
    app.dependency_overrides[get_avatar_provider] = lambda: provider
    with TestClient(app, cookies=auth_cookies) as client:
        resp = client.post("/avatar/session")

    assert resp.status_code == 503


# ── Upstream error ────────────────────────────────────────────────────────────

def test_avatar_session_returns_502_on_upstream_4xx(lifespan_mocks, auth_cookies):
    http_ctx, _ = _make_httpx_mock({}, [], token_status=401)
    with patch("app.avatar.providers.simli.httpx.AsyncClient", return_value=http_ctx):
        app = create_app()
        with TestClient(app, cookies=auth_cookies, raise_server_exceptions=False) as client:
            resp = client.post("/avatar/session")

    assert resp.status_code == 502
