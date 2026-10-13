"""Public ``PUT /u/{token}`` and ``GET /d/{token}``. The token is the permission."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote, unquote

from botocore.exceptions import BotoCoreError, ClientError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from file_mcp.config import Settings
from file_mcp.s3util import is_missing, log_s3_error
from file_mcp.tokens import InvalidToken, verify
from file_mcp.uploads import StorageError, UploadRejected, store_upload

_LOG = logging.getLogger("file_mcp.public")
_METHODS = ["GET", "PUT", "POST", "DELETE", "HEAD", "PATCH"]
_END = object()


def register_public_routes(mcp, settings: Settings, s3) -> None:
    """Register the signed upload and download paths. No bearer check."""

    @mcp.custom_route("/u/{token}", methods=_METHODS, include_in_schema=False)
    async def put_upload(request: Request) -> Response:
        if request.method != "PUT":
            return _method_not_allowed()
        secret = settings.signing_secret.get_secret_value()
        try:
            claims = verify(request.path_params["token"], "u", secret=secret)
        except InvalidToken:
            return JSONResponse({"error": "not_found"}, status_code=404)
        key = claims["k"]
        filename = claims.get("fn")
        if filename is not None and not isinstance(filename, str):
            filename = None
        media = _resolve_media(claims.get("ct"), request.headers.get("content-type"))
        if media is None:
            return JSONResponse({"error": "unsupported_content_type"}, status_code=415)
        if _declared_too_large(request, int(claims["mx"])):
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if s3 is None:
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        try:
            exists = await asyncio.to_thread(s3.head_object, Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            if not is_missing(exc):
                log_s3_error("head_object", exc)
                return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        except BotoCoreError:
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        else:
            if exists is not None:
                return JSONResponse({"error": "already_uploaded"}, status_code=409)
        try:
            size = await store_upload(
                s3,
                bucket=settings.s3_bucket,
                key=key,
                chunks=request.stream(),
                max_bytes=int(claims["mx"]),
                content_type=media,
                owner=str(claims["o"]),
                filename=filename if isinstance(filename, str) else None,
                retention_days=int(claims["rd"]),
            )
        except UploadRejected as rejected:
            return JSONResponse({"error": rejected.error}, status_code=rejected.status)
        except StorageError:
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        _LOG.info(
            "upload stored",
            extra={
                "event": "upload_stored",
                "file_id": f"eph:{key}",
                "size": size,
                "owner": claims["o"],
            },
        )
        return JSONResponse(
            {"file_id": f"eph:{key}", "size": size, "content_type": media},
            status_code=201,
        )

    @mcp.custom_route("/d/{token}", methods=_METHODS, include_in_schema=False)
    async def get_download(request: Request) -> Response:
        if request.method != "GET":
            return _method_not_allowed()
        secret = settings.signing_secret.get_secret_value()
        try:
            claims = verify(request.path_params["token"], "d", secret=secret)
        except InvalidToken:
            return JSONResponse({"error": "file_not_found"}, status_code=404)
        if s3 is None:
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        key = claims["k"]
        try:
            obj = await asyncio.to_thread(s3.get_object, Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            if is_missing(exc):
                return JSONResponse({"error": "file_not_found"}, status_code=404)
            log_s3_error("get_object", exc)
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        except BotoCoreError:
            return JSONResponse({"error": "storage_unavailable"}, status_code=503)
        body = obj["Body"]
        meta = obj.get("Metadata") or {}
        raw_name = meta.get("filename")
        if isinstance(raw_name, str) and raw_name:
            name = unquote(raw_name)
        else:
            name = key.rsplit("/", 1)[-1]
        content_type = obj.get("ContentType") or "application/octet-stream"
        size = int(obj["ContentLength"])
        headers = {
            "Content-Length": str(size),
            "Content-Disposition": _content_disposition(name),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
        }
        return StreamingResponse(
            _iter_body(body),
            status_code=200,
            media_type=content_type,
            headers=headers,
        )


def _method_not_allowed() -> JSONResponse:
    return JSONResponse({"error": "method_not_allowed"}, status_code=405)


def _resolve_media(expected: object, header: str | None) -> str | None:
    """Return the stored media type, or None when it disagrees with the token."""
    sent = _media_type(header)
    if isinstance(expected, str) and expected:
        if sent is None or sent != expected.lower():
            return None
        return expected.lower()
    return sent or "application/octet-stream"


def _media_type(header: str | None) -> str | None:
    if header is None:
        return None
    value = header.split(";", 1)[0].strip().lower()
    if value == "":
        return None
    return value


def _declared_too_large(request: Request, max_bytes: int) -> bool:
    raw = request.headers.get("content-length")
    if raw is None or raw.strip() == "":
        return False
    try:
        declared = int(raw)
    except ValueError:
        return False
    return declared > max_bytes


def _content_disposition(name: str) -> str:
    fallback = []
    for char in name:
        if 32 <= ord(char) < 127 and char not in {'"', "\\"}:
            fallback.append(char)
        else:
            fallback.append("_")
    ascii_name = "".join(fallback) or "file"
    encoded = quote(name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


async def _iter_body(body):
    iterator = body.iter_chunks(1024 * 1024)

    def _next():
        try:
            return next(iterator)
        except StopIteration:
            return _END

    try:
        while True:
            chunk = await asyncio.to_thread(_next)
            if chunk is _END:
                break
            yield chunk
    finally:
        body.close()
