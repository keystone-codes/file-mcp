"""Stateless HMAC tokens for ``/u/`` and ``/d/`` links.

The upload token carries the owner and the retention period because the
object does not exist until ``PUT`` and the service keeps no session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from file_mcp.file_ids import InvalidFileId, parse_file_id

MAX_TOKEN_LENGTH = 2048
_UPLOAD_FIELDS = frozenset({"t", "k", "o", "exp", "mx", "rd", "ct", "fn"})
_DOWNLOAD_FIELDS = frozenset({"t", "k", "exp"})


class InvalidToken(Exception):
    """The link token is rejected. The message has no detail for callers."""

    def __init__(self) -> None:
        super().__init__("invalid token")


def issue_upload(
    *,
    key: str,
    owner: str,
    exp: int,
    max_bytes: int,
    retention_days: int,
    secret: str,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    """Sign an upload token. ``rd`` is the object's retention in days."""
    fields: dict[str, object] = {
        "t": "u",
        "k": key,
        "o": owner,
        "exp": exp,
        "mx": max_bytes,
        "rd": retention_days,
    }
    if content_type is not None:
        fields["ct"] = content_type
    if filename is not None:
        fields["fn"] = filename
    return _pack(fields, secret)


def issue_download(*, key: str, exp: int, secret: str) -> str:
    """Sign a download token. It is not an upload token."""
    return _pack({"t": "d", "k": key, "exp": exp}, secret)


def verify(
    token: str,
    kind: str,
    *,
    secret: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Check a token. Any failure raises :class:`InvalidToken`."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 1:
        raise InvalidToken()
    payload, sig = token.split(".", 1)
    if payload == "" or sig == "":
        raise InvalidToken()
    expected = _sign(payload, secret)
    if not hmac.compare_digest(expected, sig):
        raise InvalidToken()
    try:
        data = json.loads(_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise InvalidToken() from None
    if not isinstance(data, dict):
        raise InvalidToken()
    _check_fields(data, kind)
    current = int(time.time()) if now is None else int(now)
    if data["exp"] < current:
        raise InvalidToken()
    try:
        parse_file_id("eph:" + data["k"])
    except InvalidFileId:
        raise InvalidToken() from None
    return data


def _pack(fields: dict[str, object], secret: str) -> str:
    raw = json.dumps(fields, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    payload = _b64encode(raw)
    token = payload + "." + _sign(payload, secret)
    if len(token) > MAX_TOKEN_LENGTH:
        raise InvalidToken()
    return token


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _check_fields(data: dict[str, Any], kind: str) -> None:
    if not isinstance(data.get("t"), str) or data["t"] != kind:
        raise InvalidToken()
    if kind == "u":
        allowed = _UPLOAD_FIELDS
        required = ("k", "o", "exp", "mx", "rd")
    elif kind == "d":
        allowed = _DOWNLOAD_FIELDS
        required = ("k", "exp")
    else:
        raise InvalidToken()
    if not set(required) <= data.keys() or not set(data) <= allowed:
        raise InvalidToken()
    if not isinstance(data["k"], str) or not _whole_number(data["exp"], low=0):
        raise InvalidToken()
    if kind == "u":
        owner = data["o"]
        if not isinstance(owner, str) or not 1 <= len(owner.encode("utf-8")) <= 254:
            raise InvalidToken()
        if not _whole_number(data["mx"], low=1) or not _whole_number(data["rd"], low=1):
            raise InvalidToken()
        if "ct" in data and not isinstance(data["ct"], str):
            raise InvalidToken()
        filename = data.get("fn")
        if filename is not None and (
            not isinstance(filename, str) or len(filename.encode("utf-8")) > 255
        ):
            raise InvalidToken()


def _whole_number(value: object, *, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low
