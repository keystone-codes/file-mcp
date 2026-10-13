"""Run one ASGI app on 127.0.0.1 for a test.

The port comes from a socket that is already bound, so nothing else can take
it between the read and the listen.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn


def bind_loopback() -> socket.socket:
    """Bind ``127.0.0.1`` on a free port and return the open socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


@asynccontextmanager
async def serve(
    app,
    sock: socket.socket | None = None,
) -> AsyncIterator[str]:
    """Serve ``app`` until the caller leaves the context.

    Pass a socket from :func:`bind_loopback` when the port has to be known
    before the app is built. Otherwise this function binds one itself.
    """
    own_socket = sock is None
    if sock is None:
        sock = bind_loopback()
    port = int(sock.getsockname()[1])
    config = uvicorn.Config(
        app,
        access_log=False,
        log_level="warning",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                error = task.exception()
                raise RuntimeError("server exited during startup") from error
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        if own_socket and not server.started:
            sock.close()
