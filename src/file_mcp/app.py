"""FastMCP server and the Starlette app in front of it."""

from __future__ import annotations

import time
from collections.abc import Callable

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from file_mcp import __version__
from file_mcp.auth import BearerMiddleware, IdentityMiddleware
from file_mcp.config import Settings
from file_mcp.health import BucketHealth
from file_mcp.logs import RequestLogMiddleware
from file_mcp.s3 import make_s3_client

# Keystone calls the service by its container DNS name.
_CONTAINER_HOST = "file-mcp"


def build_mcp(settings: Settings) -> FastMCP:
    """MCP server with identity checks. Tools are registered by later work."""
    mcp = FastMCP("file-mcp", version=__version__)
    mcp.add_middleware(IdentityMiddleware(settings))
    return mcp


def transport_kwargs(settings: Settings) -> dict:
    """HTTP options shared by the process and the tests."""
    return {
        "stateless_http": True,
        "json_response": True,
        "middleware": [
            Middleware(RequestLogMiddleware),
            Middleware(BearerMiddleware, settings=settings),
        ],
        "host_origin_protection": True,
        "allowed_hosts": _allowed_hosts(settings),
    }


def build_app(settings: Settings, s3=None, *, clock: Callable[[], float] = time.monotonic):
    """Starlette app: MCP on ``/mcp``, health on ``/healthz``."""
    mcp = build_mcp(settings)
    client = s3 if s3 is not None else make_s3_client(settings)
    health = BucketHealth(client, settings.s3_bucket, clock=clock)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def _healthz(_request: Request) -> JSONResponse:
        if await health.reachable():
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "degraded", "s3": "unreachable"}, status_code=503)

    app = mcp.http_app(path="/mcp", **transport_kwargs(settings))
    app.state.health = health
    app.state.s3_client = client
    return app


def _allowed_hosts(settings: Settings) -> list[str]:
    hosts = [_CONTAINER_HOST]
    if settings.registry_name not in hosts:
        hosts.append(settings.registry_name)
    return hosts
