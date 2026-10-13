"""Request logs."""

from __future__ import annotations

import io
import json
import logging

import httpx2
import pytest
from tests.support.env import load_required

from file_mcp.app import build_app
from file_mcp.logs import JsonFormatter, redact_path


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


def test_redact_path_hides_escaped_and_uppercase_links() -> None:
    secret = "link-token"
    for path in (
        f"/u/{secret}/../../healthz",
        f"/d/{secret}/../../healthz",
        f"/u/{secret}%2f..%2f..%2fhealthz",
        f"/d/{secret}%2f..%2f..%2fhealthz",
        f"/u/{secret}%2F..%2F..%2Fhealthz",
        f"/d/{secret}%2F..%2F..%2Fhealthz",
        f"/U/{secret}/../../healthz",
        f"/D/{secret}%2f..%2f..%2fhealthz",
    ):
        redacted = redact_path(path)
        assert secret not in redacted
        assert redacted == "/healthz"
        assert path not in redacted
    assert redact_path(f"/U/{secret}") == "/u/***"
    assert redact_path(f"/D/{secret}") == "/d/***"
    assert redact_path(f"/./U/{secret}") == "/u/***"
    assert redact_path(f"//D/{secret}") == "/d/***"
    assert redact_path(f"/x/../U/{secret}") == "/u/***"
    nested = f"/healthz/u/{secret}"
    assert secret not in redact_path(nested)
    assert redact_path(nested) != nested


def test_redact_path_hides_dot_segments() -> None:
    secret = "dot-segment-token"
    for path in (
        f"/./u/{secret}",
        f"//u/{secret}",
        f"/x/../u/{secret}",
        f"/u/../d/{secret}",
        f"/%2e%2e/u/{secret}",
        f"/healthz/../d/{secret}",
    ):
        redacted = redact_path(path)
        assert secret not in redacted
        assert redacted in {"/u/***", "/d/***"}
    assert redact_path("/healthz") == "/healthz"
    assert redact_path("/upload") == "/upload"
    assert redact_path("/download") == "/download"


async def test_request_log_redacts_dot_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "dot-segment-token"
    settings = load_required(monkeypatch)
    app = build_app(settings, s3=_Quiet())
    logger, handler, stream = _capture_access()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    try:
        for path in (f"/./u/{secret}", f"//u/{secret}", f"/x/../d/{secret}"):
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [(b"host", b"127.0.0.1:8081")],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 8081),
                "root_path": "",
            }
            await app(scope, receive, send)
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert secret not in text
    assert '"path": "/u/***"' in text
    assert '"path": "/d/***"' in text


async def test_request_log_redacts_escaped_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "link-token"
    settings = load_required(monkeypatch)
    app = build_app(settings, s3=_Quiet())
    logger, handler, stream = _capture_access()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    path = f"/u/{secret}%2f..%2f..%2fhealthz"
    try:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:8081")],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8081),
            "root_path": "",
        }
        await app(scope, receive, send)
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert secret not in text
    assert path not in text
    assert '"path": "/healthz"' in text


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
