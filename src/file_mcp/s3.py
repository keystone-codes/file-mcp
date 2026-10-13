"""S3 client. The service never creates a bucket."""

from __future__ import annotations

import logging
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from file_mcp.config import Settings

_LOG = logging.getLogger("file_mcp.s3")


def make_s3_client(settings: Settings):
    """Build a path-style or virtual-hosted S3 client from settings."""
    addressing = "path" if settings.s3_path_style else "virtual"
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        config=Config(
            s3={"addressing_style": addressing},
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        ),
    )


def wait_for_bucket(client, settings: Settings) -> None:
    """Block until ``head_bucket`` succeeds, or exit 3.

    A missing bucket or a denied key fails immediately. Network errors and
    HTTP 5xx are retried until ``FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS``.
    """
    timeout = settings.s3_startup_timeout_seconds
    deadline = time.monotonic() + timeout
    delay = 1.0
    while True:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
        except ClientError as exc:
            kind = _client_error_kind(exc)
            if kind == "missing":
                _fail(
                    f"bucket {settings.s3_bucket} does not exist; "
                    "the installer creates it during resource provisioning"
                )
            if kind == "denied":
                _fail(f"access to bucket {settings.s3_bucket} denied; check FILE_MCP_S3_ACCESS_KEY")
            if kind == "retry":
                _retry_or_fail(settings, deadline, delay)
                delay = min(delay * 2, 5.0)
                continue
            _fail("S3 bucket check failed")
        except BotoCoreError:
            _retry_or_fail(settings, deadline, delay)
            delay = min(delay * 2, 5.0)
        else:
            _LOG.info("S3 bucket is reachable", extra={"event": "s3_ready"})
            return


def _retry_or_fail(settings: Settings, deadline: float, delay: float) -> None:
    now = time.monotonic()
    if now >= deadline:
        timeout = settings.s3_startup_timeout_seconds
        _fail(f"S3 endpoint {settings.s3_endpoint} unreachable after {timeout}s")
    remaining = deadline - now
    time.sleep(min(delay, 5.0, remaining))


def _client_error_kind(exc: ClientError) -> str:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if status == 404 or code in {"404", "NoSuchBucket", "NotFound"}:
        return "missing"
    if status == 403 or code in {"403", "AccessDenied", "Forbidden"}:
        return "denied"
    if isinstance(status, int) and status >= 500:
        return "retry"
    return "fatal"


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(3)
