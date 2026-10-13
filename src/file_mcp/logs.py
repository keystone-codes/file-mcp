"""JSON logs on stdout. Tokens in ``/u/`` and ``/d/`` paths are redacted."""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_QUIET_LOGGERS = ("boto3", "botocore", "urllib3", "httpx2")
_ACCESS = logging.getLogger("file_mcp.access")


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, event, msg, plus extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_") or key in payload:
                continue
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Send JSON logs to stdout. AWS and HTTP client loggers stay at WARNING."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def redact_path(path: str) -> str:
    """Hide link tokens. ``/u/<token>`` and ``/d/<token>`` become ``/u/***`` and ``/d/***``."""
    for prefix in ("/u", "/d"):
        if path == prefix or path.startswith(prefix + "/"):
            return f"{prefix}/***"
    return path


class RequestLogMiddleware:
    """Log one JSON line per HTTP response, including 401 from the bearer check."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            _ACCESS.info(
                "request",
                extra={
                    "event": "http_request",
                    "method": scope.get("method", ""),
                    "path": redact_path(scope.get("path", "")),
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )
