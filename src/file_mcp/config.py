"""Process settings. Secrets stay in ``SecretStr`` and never appear in errors."""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG = logging.getLogger("file_mcp.config")

_SECRET_MIN_BYTES = 32
_S3_ENDPOINT_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_S3_REGION_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_S3_KEY_RE = re.compile(r"^\S{1,128}$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class Settings(BaseSettings):
    """File-mcp settings. Build with :func:`load_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="FILE_MCP_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    host: str = "0.0.0.0"
    port: int = 8081
    public_base_url: str
    registry_name: str = "file-mcp"
    bearer_token: SecretStr = Field(repr=False)
    identity_secret: SecretStr = Field(repr=False)
    identity_secret_previous: SecretStr = Field(default=SecretStr(""), repr=False)
    identity_issuer: str = ""
    signing_secret: SecretStr = Field(repr=False)
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: SecretStr = Field(repr=False)
    s3_secret_key: SecretStr = Field(repr=False)
    s3_region: str = "us-east-1"
    s3_path_style: bool = True
    s3_startup_timeout_seconds: int = 60
    link_ttl_seconds: int = 900
    link_ttl_max_seconds: int = 86400
    upload_ttl_days: int = 30
    upload_ttl_max_days: int = 3650
    max_bytes: int = 104_857_600
    log_level: str = "INFO"

    def __repr__(self) -> str:
        return (
            "Settings("
            f"host={self.host!r}, port={self.port!r}, "
            f"public_base_url={self.public_base_url!r}, "
            f"registry_name={self.registry_name!r}, "
            f"identity_issuer={self.identity_issuer!r}, "
            f"s3_endpoint={self.s3_endpoint!r}, s3_bucket={self.s3_bucket!r}, "
            f"s3_region={self.s3_region!r}, s3_path_style={self.s3_path_style!r}, "
            f"s3_startup_timeout_seconds={self.s3_startup_timeout_seconds!r}, "
            f"link_ttl_seconds={self.link_ttl_seconds!r}, "
            f"link_ttl_max_seconds={self.link_ttl_max_seconds!r}, "
            f"upload_ttl_days={self.upload_ttl_days!r}, "
            f"upload_ttl_max_days={self.upload_ttl_max_days!r}, "
            f"max_bytes={self.max_bytes!r}, log_level={self.log_level!r})"
        )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read ``FILE_MCP_*`` from the environment.

    Every problem is reported together. On failure the process exits with
    code 2. Secret values are never included in the text.
    """
    source = os.environ if environ is None else environ
    problems, values = _parse(source)
    if problems:
        text = "\n".join(f"config error: {name}: {reason}" for name, reason in problems)
        print(text, file=sys.stderr)
        raise SystemExit(2)
    return Settings.model_construct(**values)


def _parse(environ: Mapping[str, str]) -> tuple[list[tuple[str, str]], dict[str, object]]:
    problems: list[tuple[str, str]] = []
    values: dict[str, object] = {}

    def raw(name: str) -> str | None:
        if name not in environ:
            return None
        return environ[name]

    def required(name: str) -> str | None:
        value = raw(name)
        if value is None or value == "":
            problems.append((name, "is required"))
            return None
        return value

    def optional_text(name: str, default: str) -> str:
        value = raw(name)
        if value is None or value.strip() == "":
            return default
        return value.strip()

    host = optional_text("FILE_MCP_HOST", "0.0.0.0")
    if not host:
        problems.append(("FILE_MCP_HOST", "must not be empty"))
    values["host"] = host

    values["port"] = _bounded_int(
        problems,
        "FILE_MCP_PORT",
        raw("FILE_MCP_PORT"),
        default=8081,
        low=1,
        high=65535,
    )

    public = required("FILE_MCP_PUBLIC_BASE_URL")
    if public is not None:
        normalized = _public_base_url(problems, public.strip())
        values["public_base_url"] = normalized or ""
    else:
        values["public_base_url"] = ""

    registry = optional_text("FILE_MCP_REGISTRY_NAME", "file-mcp")
    if not registry:
        problems.append(("FILE_MCP_REGISTRY_NAME", "must not be empty"))
    values["registry_name"] = registry

    for name, field in (
        ("FILE_MCP_BEARER_TOKEN", "bearer_token"),
        ("FILE_MCP_IDENTITY_SECRET", "identity_secret"),
        ("FILE_MCP_SIGNING_SECRET", "signing_secret"),
    ):
        secret = required(name)
        if secret is not None:
            _check_secret_length(problems, name, secret)
            values[field] = SecretStr(secret)
        else:
            values[field] = SecretStr("")

    previous = raw("FILE_MCP_IDENTITY_SECRET_PREVIOUS")
    if previous is None or previous == "":
        values["identity_secret_previous"] = SecretStr("")
    else:
        _check_secret_length(problems, "FILE_MCP_IDENTITY_SECRET_PREVIOUS", previous)
        values["identity_secret_previous"] = SecretStr(previous)

    issuer = raw("FILE_MCP_IDENTITY_ISSUER")
    values["identity_issuer"] = "" if issuer is None else issuer.strip()

    endpoint = required("FILE_MCP_S3_ENDPOINT")
    if endpoint is not None:
        endpoint = endpoint.strip()
        if _S3_ENDPOINT_RE.fullmatch(endpoint) is None:
            problems.append(("FILE_MCP_S3_ENDPOINT", "must be http(s)://host[:port] with no path"))
        values["s3_endpoint"] = endpoint
    else:
        values["s3_endpoint"] = ""

    bucket = required("FILE_MCP_S3_BUCKET")
    if bucket is not None:
        bucket = bucket.strip()
        if _S3_BUCKET_RE.fullmatch(bucket) is None:
            problems.append(
                (
                    "FILE_MCP_S3_BUCKET",
                    "must be 3–63 characters of a-z, 0-9 and hyphens, with no dots",
                )
            )
        values["s3_bucket"] = bucket
    else:
        values["s3_bucket"] = ""

    for name, field in (
        ("FILE_MCP_S3_ACCESS_KEY", "s3_access_key"),
        ("FILE_MCP_S3_SECRET_KEY", "s3_secret_key"),
    ):
        key = required(name)
        if key is not None:
            if _S3_KEY_RE.fullmatch(key) is None:
                problems.append((name, "must be 1–128 non-space characters"))
            values[field] = SecretStr(key)
        else:
            values[field] = SecretStr("")

    region = optional_text("FILE_MCP_S3_REGION", "us-east-1")
    if _S3_REGION_RE.fullmatch(region) is None:
        problems.append(("FILE_MCP_S3_REGION", "must be 1–32 characters of a-z, 0-9 and hyphens"))
    values["s3_region"] = region

    values["s3_path_style"] = _bool_value(
        problems, "FILE_MCP_S3_PATH_STYLE", raw("FILE_MCP_S3_PATH_STYLE"), default=True
    )
    values["s3_startup_timeout_seconds"] = _bounded_int(
        problems,
        "FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS",
        raw("FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS"),
        default=60,
        low=0,
        high=600,
    )
    values["link_ttl_seconds"] = _bounded_int(
        problems,
        "FILE_MCP_LINK_TTL_SECONDS",
        raw("FILE_MCP_LINK_TTL_SECONDS"),
        default=900,
        low=60,
        high=86400,
    )
    values["link_ttl_max_seconds"] = _bounded_int(
        problems,
        "FILE_MCP_LINK_TTL_MAX_SECONDS",
        raw("FILE_MCP_LINK_TTL_MAX_SECONDS"),
        default=86400,
        low=1,
        high=86400,
    )
    values["upload_ttl_days"] = _bounded_int(
        problems,
        "FILE_MCP_UPLOAD_TTL_DAYS",
        raw("FILE_MCP_UPLOAD_TTL_DAYS"),
        default=30,
        low=1,
        high=3650,
    )
    values["upload_ttl_max_days"] = _bounded_int(
        problems,
        "FILE_MCP_UPLOAD_TTL_MAX_DAYS",
        raw("FILE_MCP_UPLOAD_TTL_MAX_DAYS"),
        default=3650,
        low=1,
        high=3650,
    )
    if values["link_ttl_max_seconds"] < values["link_ttl_seconds"]:
        problems.append(
            (
                "FILE_MCP_LINK_TTL_MAX_SECONDS",
                "must be at least FILE_MCP_LINK_TTL_SECONDS",
            )
        )
    if values["upload_ttl_max_days"] < values["upload_ttl_days"]:
        problems.append(
            (
                "FILE_MCP_UPLOAD_TTL_MAX_DAYS",
                "must be at least FILE_MCP_UPLOAD_TTL_DAYS",
            )
        )
    values["max_bytes"] = _bounded_int(
        problems,
        "FILE_MCP_MAX_BYTES",
        raw("FILE_MCP_MAX_BYTES"),
        default=104_857_600,
        low=1,
        high=5_368_709_120,
    )

    level = optional_text("FILE_MCP_LOG_LEVEL", "INFO").upper()
    if level not in _LOG_LEVELS:
        problems.append(("FILE_MCP_LOG_LEVEL", "must be DEBUG, INFO, WARNING, or ERROR"))
    values["log_level"] = level

    return problems, values


def _check_secret_length(problems: list[tuple[str, str]], name: str, value: str) -> None:
    if len(value.encode("utf-8")) < _SECRET_MIN_BYTES:
        problems.append((name, f"must be at least {_SECRET_MIN_BYTES} bytes"))


def _public_base_url(problems: list[tuple[str, str]], value: str) -> str | None:
    name = "FILE_MCP_PUBLIC_BASE_URL"
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        problems.append((name, "must be an http or https URL with a host"))
        return None
    if parts.username or parts.password:
        problems.append((name, "must not include userinfo"))
        return None
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        problems.append((name, "must not include a path, query, or fragment"))
        return None
    host = parts.hostname
    netloc = parts.netloc
    normalized = f"{parts.scheme}://{netloc}"
    if parts.scheme == "http" and host not in _LOOPBACK_HOSTS:
        _LOG.warning(
            "FILE_MCP_PUBLIC_BASE_URL uses http for a non-loopback host",
            extra={"event": "insecure_public_base_url", "host": host},
        )
    return normalized


def _bounded_int(
    problems: list[tuple[str, str]],
    name: str,
    raw_value: str | None,
    *,
    default: int,
    low: int,
    high: int,
) -> int:
    if raw_value is None or raw_value.strip() == "":
        return default
    text = raw_value.strip()
    if not re.fullmatch(r"[0-9]+", text):
        problems.append((name, f"must be an integer from {low} to {high}"))
        return default
    number = int(text)
    if number < low or number > high:
        problems.append((name, f"must be an integer from {low} to {high}"))
        return default
    return number


def _bool_value(
    problems: list[tuple[str, str]],
    name: str,
    raw_value: str | None,
    *,
    default: bool,
) -> bool:
    if raw_value is None or raw_value.strip() == "":
        return default
    text = raw_value.strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    problems.append((name, "must be true or false"))
    return default
