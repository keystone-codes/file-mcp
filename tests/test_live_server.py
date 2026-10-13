"""The live server uses the port of a socket that is already open."""

from __future__ import annotations

import httpx2
from tests.support.live_server import bind_loopback, serve


async def _ok(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def test_serve_uses_the_bound_port() -> None:
    sock = bind_loopback()
    expected = f"http://127.0.0.1:{sock.getsockname()[1]}"
    async with serve(_ok, sock) as base:
        assert base == expected
        async with httpx2.AsyncClient() as http:
            response = await http.get(base + "/healthz")
    assert response.status_code == 204
