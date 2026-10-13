"""Bearer check (ASGI) and signed identity check (tool-call middleware)."""

from __future__ import annotations

import hmac
import logging
from contextvars import ContextVar

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from file_mcp._vendor.keystone_identity import (
    IdentityError,
    KeystoneIdentity,
    verify_keystone_identity,
)
from file_mcp.config import Settings
from file_mcp.errors import tool_error

_LOG = logging.getLogger("file_mcp.auth")
_IDENTITY: ContextVar[KeystoneIdentity | None] = ContextVar("file_mcp_identity", default=None)


def current_identity() -> KeystoneIdentity:
    """Identity verified for the tool call in progress."""
    identity = _IDENTITY.get()
    if identity is None:
        raise RuntimeError("verified identity is not available")
    return identity


def _tokens_equal(got: bytes, expected: bytes) -> bool:
    if len(got) != len(expected):
        hmac.compare_digest(expected, expected)
        return False
    return hmac.compare_digest(got, expected)


class BearerMiddleware:
    """Require ``Authorization: Bearer`` on ``/mcp``. Other paths stay open.

    Checked here, not in FastMCP middleware: ``get_http_headers()`` strips
    ``authorization`` before a tool runs.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._expected = settings.bearer_token.get_secret_value().encode("utf-8")

    def __repr__(self) -> str:
        return "BearerMiddleware()"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if not _bearer_matches(scope, self._expected):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _bearer_matches(scope: Scope, expected: bytes) -> bool:
    header = _header(scope, b"authorization")
    if header is None:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token == "" or token != token.strip():
        return False
    return _tokens_equal(token.encode("utf-8"), expected)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class IdentityMiddleware(Middleware):
    """Verify ``X-Keystone-Identity`` on ``tools/call`` and publish the subject.

    ``tools/list`` is not checked: Keystone does not send an assertion for discovery.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        tool_name = _tool_name(context)
        try:
            identity = _verify(self._settings, tool_name)
        except IdentityError as exc:
            _LOG.info(
                "tool call rejected",
                extra={"event": "tool_call", "tool": tool_name, "outcome": "rejected"},
            )
            raise tool_error("identity_rejected", str(exc)) from exc

        token = _IDENTITY.set(identity)
        try:
            result = await call_next(context)
        except Exception:
            _LOG.info(
                "tool call failed",
                extra={
                    "event": "tool_call",
                    "tool": tool_name,
                    "sub": identity.sub,
                    "outcome": "error",
                },
            )
            raise
        else:
            _LOG.info(
                "tool call",
                extra={
                    "event": "tool_call",
                    "tool": tool_name,
                    "sub": identity.sub,
                    "outcome": "ok",
                },
            )
            return result
        finally:
            _IDENTITY.reset(token)


def _tool_name(context: MiddlewareContext) -> str:
    name = getattr(context.message, "name", None)
    if isinstance(name, str) and name:
        return name
    return ""


def _verify(settings: Settings, tool_name: str) -> KeystoneIdentity:
    header = get_http_headers().get("x-keystone-identity")
    token = header.strip() if isinstance(header, str) else header
    secrets = [settings.identity_secret.get_secret_value()]
    previous = settings.identity_secret_previous.get_secret_value()
    if previous:
        secrets.append(previous)
    identity = verify_keystone_identity(
        token,
        secrets=secrets,
        audience=settings.registry_name,
        issuer=settings.identity_issuer or None,
    )
    if identity.mcp_method != "tools/call":
        raise IdentityError("assertion is not for tools/call")
    if identity.mcp_target != tool_name:
        raise IdentityError("assertion is for a different tool")
    return identity
