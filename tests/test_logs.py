"""Request logs."""

from __future__ import annotations

import io
import json
import logging

import httpx2
import pytest
from tests.support.env import load_required

from file_mcp.app import build_app
from file_mcp.logs import JsonFormatter


class _Quiet:
    def head_bucket(self, **_kwargs: object) -> None:
        return None


def _capture_access():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("file_mcp.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, handler, stream


async def test_request_log_redacts_token_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    app = build_app(settings, s3=_Quiet())
    logger, handler, stream = _capture_access()
    try:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8081"
        ) as http:
            response = await http.put("/u/abc.def")
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 404
    text = stream.getvalue()
    assert '"path": "/u/***"' in text
    assert "abc.def" not in text


async def test_log_lines_are_json(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    app = build_app(settings, s3=_Quiet())
    logger, handler, stream = _capture_access()
    try:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8081"
        ) as http:
            await http.get("/healthz")
    finally:
        logger.removeHandler(handler)
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines
    for line in lines:
        payload = json.loads(line)
        assert {"ts", "level", "logger", "event", "msg"} <= payload.keys()
