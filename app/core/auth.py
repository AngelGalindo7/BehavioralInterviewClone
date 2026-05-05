"""
Single-passcode access gate.

A successful POST /auth/login sets a signed cookie via itsdangerous.
The middleware below validates that cookie on every subsequent request
(both HTTP and WebSocket) outside a small public allowlist.
"""
import secrets
from http.cookies import SimpleCookie

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset({"/health", "/ready"})
_PUBLIC_PREFIXES: tuple[str, ...] = ("/auth/",)
_SERIALIZER_SALT = "bd-auth-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_SERIALIZER_SALT)


def issue_token() -> str:
    """Sign a fresh session token. Payload is intentionally minimal — the
    cookie's existence + valid signature + non-expiry is the whole signal."""
    return _serializer().dumps({"v": 1})


def verify_token(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=settings.auth_cookie_max_age_seconds)
        return True
    except (BadSignature, SignatureExpired):
        return False


def verify_passcode(submitted: str) -> bool:
    # Constant-time compare to avoid timing oracles on the shared secret.
    return secrets.compare_digest(
        submitted.encode("utf-8"),
        settings.access_passcode.encode("utf-8"),
    )


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _extract_cookie(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name != b"cookie":
            continue
        jar = SimpleCookie()
        jar.load(value.decode("latin-1"))
        morsel = jar.get(settings.auth_cookie_name)
        if morsel is not None:
            return morsel.value
    return None


class AccessPasscodeMiddleware:
    """ASGI middleware that requires a valid signed-session cookie on every
    request except the public allowlist. CORS preflight (OPTIONS) is allowed
    through so the CORSMiddleware can answer it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if _is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        token = _extract_cookie(scope)
        if token and verify_token(token):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # 4401 is a custom close code; the frontend treats it as "go to login".
            await send({"type": "websocket.close", "code": 4401})
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"unauthenticated"}',
        })
