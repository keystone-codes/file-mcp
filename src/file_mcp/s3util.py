"""Shared S3 error classification. Details stay in the log."""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

_LOG = logging.getLogger("file_mcp.s3")
_MISSING = frozenset({"404", "NoSuchKey", "NotFound"})


def error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def status_code(exc: ClientError) -> int | None:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if isinstance(status, int):
        return status
    return None


def is_missing(exc: ClientError) -> bool:
    return status_code(exc) == 404 or error_code(exc) in _MISSING


def log_s3_error(operation: str, exc: ClientError) -> None:
    """Log the operation and the S3 code. The message is fixed on purpose."""
    _LOG.warning(
        "storage request failed",
        extra={"event": "s3_error", "operation": operation, "error_code": error_code(exc)},
    )
