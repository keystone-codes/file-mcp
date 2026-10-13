"""File tools. Bytes never travel in the arguments."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import NotRequired, TypedDict
from urllib.parse import unquote

from botocore.exceptions import BotoCoreError, ClientError

from file_mcp.auth import current_identity
from file_mcp.config import Settings
from file_mcp.errors import tool_error
from file_mcp.file_ids import InvalidFileId, new_object_key, parse_file_id
from file_mcp.lifecycle import RETENTION_TAG
from file_mcp.s3util import is_missing, log_s3_error
from file_mcp.tokens import InvalidToken, issue_download, issue_upload

_NOT_YOURS = "file not found or not yours"
_STORAGE = "storage is temporarily unavailable"
_CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,63}/[A-Za-z0-9!#$&^_.+-]{1,63}$")

_CREATE = (
    "Create a short-lived signed URL for uploading one file. "
    "PUT the file bytes to upload_url (for example with curl -T), then pass file_id "
    "to the tool that needs the file. Only you can later get a download link for this "
    "file. Never put file bytes into tool arguments."
)
_DOWNLOAD = (
    "Create a short-lived signed download link for a file you uploaded. "
    "Anyone holding the link can download the file until expires_at, so pass it only "
    "to the person or service that needs it."
)
_INFO = (
    "Show name, type, size and expiry of a file you uploaded. "
    "The storage does not delete the file before expires_at."
)
_UPLOAD_ANNOTATIONS = {
    "readOnlyHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "openWorldHint": False,
}


class UploadUrlResult(TypedDict):
    upload_url: str
    file_id: str
    expires_at: str
    max_bytes: int
    retention_until: str
    warnings: NotRequired[list[str]]


class DownloadLinkResult(TypedDict):
    file_url: str
    expires_at: str


class FileInfoResult(TypedDict):
    file_id: str
    filename: str | None
    content_type: str
    size: int
    created_at: str
    expires_at: str


def register_tools(mcp, settings: Settings, s3, *, lifecycle_supported: bool) -> None:
    """Register the three file tools on ``mcp``."""

    @mcp.tool(description=_CREATE, annotations=_UPLOAD_ANNOTATIONS)
    async def create_upload_url(
        filename: str | None = None,
        content_type: str | None = None,
        link_ttl_seconds: int | None = None,
        retention_days: int | None = None,
    ) -> UploadUrlResult:
        cleaned = _clean_filename(filename)
        media = _clean_content_type(content_type)
        ttl = _link_ttl(link_ttl_seconds, settings)
        retention = _retention_days(retention_days, settings)
        owner = current_identity().sub
        if owner == "" or len(owner.encode("utf-8")) > 254:
            raise tool_error("invalid_argument", "owner subject must be 1 to 254 bytes")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        exp = int(now.timestamp()) + ttl
        key = new_object_key(cleaned)
        secret = settings.signing_secret.get_secret_value()
        try:
            token = issue_upload(
                key=key,
                owner=owner,
                exp=exp,
                max_bytes=settings.max_bytes,
                retention_days=retention,
                secret=secret,
                content_type=media,
                filename=cleaned,
            )
        except InvalidToken:
            raise tool_error(
                "invalid_argument", "link token would exceed 2048 characters"
            ) from None
        result: UploadUrlResult = {
            "upload_url": f"{settings.public_base_url}/u/{token}",
            "file_id": f"eph:{key}",
            "expires_at": _format(now + timedelta(seconds=ttl)),
            "max_bytes": settings.max_bytes,
            "retention_until": _format(now + timedelta(days=retention)),
        }
        if not lifecycle_supported:
            result["warnings"] = ["retention_unsupported"]
        return result

    @mcp.tool(description=_DOWNLOAD, annotations=_READ_ANNOTATIONS)
    async def get_download_link(
        file_id: str,
        link_ttl_seconds: int | None = None,
    ) -> DownloadLinkResult:
        key, _head = _owned_head(s3, settings, file_id, "head_object")
        ttl = _link_ttl(link_ttl_seconds, settings)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        exp = int(now.timestamp()) + ttl
        secret = settings.signing_secret.get_secret_value()
        try:
            token = issue_download(key=key, exp=exp, secret=secret)
        except InvalidToken:
            raise tool_error(
                "invalid_argument", "link token would exceed 2048 characters"
            ) from None
        return {
            "file_url": f"{settings.public_base_url}/d/{token}",
            "expires_at": _format(now + timedelta(seconds=ttl)),
        }

    @mcp.tool(description=_INFO, annotations=_READ_ANNOTATIONS)
    async def get_file_info(file_id: str) -> FileInfoResult:
        key, head = _owned_head(s3, settings, file_id, "head_object")
        created = _as_utc(head["LastModified"]).replace(microsecond=0)
        retention = _retention_from_tags(s3, settings, key)
        filename = _filename_from_head(head)
        content_type = head.get("ContentType") or "application/octet-stream"
        return {
            "file_id": file_id,
            "filename": filename,
            "content_type": content_type,
            "size": int(head["ContentLength"]),
            "created_at": _format(created),
            "expires_at": _format(created + timedelta(days=retention)),
        }


def _owned_head(client, settings: Settings, file_id: str, operation: str) -> tuple[str, dict]:
    """Head the object when the caller owns it.

    A missing object and a different owner produce the same error so a caller
    cannot learn whether someone else's file exists. Roles are not consulted.
    """
    try:
        key = parse_file_id(file_id)
    except InvalidFileId:
        raise tool_error("invalid_file_id", "invalid file id") from None
    if client is None:
        raise tool_error("storage_unavailable", _STORAGE)
    try:
        head = client.head_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        if is_missing(exc):
            raise tool_error("file_not_found", _NOT_YOURS) from None
        log_s3_error(operation, exc)
        raise tool_error("storage_unavailable", _STORAGE) from None
    except BotoCoreError:
        raise tool_error("storage_unavailable", _STORAGE) from None
    raw_owner = (head.get("Metadata") or {}).get("owner")
    if not isinstance(raw_owner, str) or unquote(raw_owner) != current_identity().sub:
        raise tool_error("file_not_found", _NOT_YOURS)
    return key, head


def _retention_from_tags(client, settings: Settings, key: str) -> int:
    """Days stamped on the object, or the configured upload lifetime."""
    try:
        tagging = client.get_object_tagging(Bucket=settings.s3_bucket, Key=key)
    except (ClientError, BotoCoreError):
        return settings.upload_ttl_days
    for tag in tagging.get("TagSet") or []:
        if tag.get("Key") != RETENTION_TAG:
            continue
        value = tag.get("Value")
        if isinstance(value, str) and value.isdecimal():
            days = int(value)
            if days >= 1:
                return days
    return settings.upload_ttl_days


def _filename_from_head(head: dict) -> str | None:
    raw = (head.get("Metadata") or {}).get("filename")
    if not isinstance(raw, str) or raw == "":
        return None
    return unquote(raw)


def _clean_filename(value: str | None) -> str | None:
    if value is None:
        return None
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cc")
    name = name.strip()
    if name == "":
        return None
    if len(name.encode("utf-8")) > 255:
        raise tool_error("invalid_argument", "filename must be at most 255 bytes of UTF-8")
    return name


def _clean_content_type(value: str | None) -> str | None:
    if value is None:
        return None
    if _CONTENT_TYPE_RE.fullmatch(value) is None:
        raise tool_error(
            "invalid_argument",
            "content_type must be a type/subtype without parameters",
        )
    return value.lower()


def _link_ttl(value: int | None, settings: Settings) -> int:
    if value is None:
        return settings.link_ttl_seconds
    high = settings.link_ttl_max_seconds
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > high:
        raise tool_error(
            "invalid_argument",
            f"link_ttl_seconds must be an integer from 1 to {high}",
        )
    return value


def _retention_days(value: int | None, settings: Settings) -> int:
    if value is None:
        return settings.upload_ttl_days
    high = settings.upload_ttl_max_days
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > high:
        raise tool_error(
            "invalid_argument",
            f"retention_days must be an integer from 1 to {high}",
        )
    return value


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _format(moment: datetime) -> str:
    return _as_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")
