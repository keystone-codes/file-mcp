"""Stream an upload into S3 without holding the whole body.

Only one 8 MiB part is buffered. A body that fits in that part is a single
``put_object``. A larger body is a multipart upload; every part except the
last is 8 MiB, which is above S3's 5 MiB minimum.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from file_mcp.lifecycle import RETENTION_TAG, ensure_object_retention
from file_mcp.s3util import log_s3_error

_LOG = logging.getLogger("file_mcp.uploads")

PART_SIZE = 8 * 1024 * 1024

# Largest buffer held during the most recent :func:`store_upload`.
peak_buffer_bytes = 0


class UploadRejected(Exception):
    """The body itself is refused. ``error`` is the public JSON code."""

    def __init__(self, status: int, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


class StorageError(Exception):
    """S3 failed while storing the object."""


def reset_peak_buffer() -> None:
    global peak_buffer_bytes
    peak_buffer_bytes = 0


async def store_upload(
    client,
    *,
    bucket: str,
    key: str,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    content_type: str,
    owner: str,
    filename: str | None,
    retention_days: int,
) -> int:
    """Store the stream and return the byte count.

    Raises :class:`UploadRejected` for an empty or oversized body and
    :class:`StorageError` when S3 fails. Other exceptions abort a multipart
    upload and propagate (the client went away).
    """
    global peak_buffer_bytes
    peak_buffer_bytes = 0
    state = _State()
    metadata = {"owner": quote(owner, safe="")}
    if filename:
        metadata["filename"] = quote(filename, safe="")
    tagging = f"{RETENTION_TAG}={retention_days}"
    try:
        async for chunk in chunks:
            if not chunk:
                continue
            offset = 0
            while offset < len(chunk):
                if state.upload_id is None and len(state.buffer) == PART_SIZE:
                    await _begin(client, state, bucket, key, content_type, metadata, tagging)
                space = PART_SIZE - len(state.buffer)
                piece = chunk[offset : offset + space]
                offset += len(piece)
                if state.total + len(piece) > max_bytes:
                    await _abort(client, state, bucket, key)
                    raise UploadRejected(413, "payload_too_large")
                state.buffer.extend(piece)
                state.total += len(piece)
                _note(len(state.buffer))
                if state.upload_id is not None and len(state.buffer) == PART_SIZE:
                    await _upload_part(client, state, bucket, key)
        if state.total == 0:
            raise UploadRejected(422, "empty_body")
        if state.upload_id is None:
            await asyncio.to_thread(
                client.put_object,
                Bucket=bucket,
                Key=key,
                Body=bytes(state.buffer),
                ContentType=content_type,
                Metadata=metadata,
                Tagging=tagging,
            )
            state.buffer.clear()
        else:
            if state.buffer:
                await _upload_part(client, state, bucket, key)
            await asyncio.to_thread(
                client.complete_multipart_upload,
                Bucket=bucket,
                Key=key,
                UploadId=state.upload_id,
                MultipartUpload={"Parts": state.parts},
            )
    except UploadRejected:
        raise
    except (ClientError, BotoCoreError) as exc:
        await _abort(client, state, bucket, key)
        if isinstance(exc, ClientError):
            log_s3_error("upload", exc)
        raise StorageError() from exc
    except Exception:
        await _abort(client, state, bucket, key)
        raise
    # Retention is best-effort: the bytes are already stored.
    await asyncio.to_thread(ensure_object_retention, client, bucket, retention_days)
    return state.total


class _State:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.total = 0
        self.upload_id: str | None = None
        self.parts: list[dict[str, object]] = []
        self.part_number = 1


def _note(size: int) -> None:
    global peak_buffer_bytes
    if size > peak_buffer_bytes:
        peak_buffer_bytes = size


async def _begin(
    client, state: _State, bucket: str, key: str, content_type: str, metadata, tagging
):
    response = await asyncio.to_thread(
        client.create_multipart_upload,
        Bucket=bucket,
        Key=key,
        ContentType=content_type,
        Metadata=metadata,
        Tagging=tagging,
    )
    state.upload_id = response["UploadId"]
    await _upload_part(client, state, bucket, key)


async def _upload_part(client, state: _State, bucket: str, key: str) -> None:
    payload = bytes(state.buffer)
    state.buffer.clear()
    _note(len(payload))
    response = await asyncio.to_thread(
        client.upload_part,
        Bucket=bucket,
        Key=key,
        UploadId=state.upload_id,
        PartNumber=state.part_number,
        Body=payload,
    )
    state.parts.append({"ETag": response["ETag"], "PartNumber": state.part_number})
    state.part_number += 1


async def _abort(client, state: _State, bucket: str, key: str) -> None:
    upload_id = state.upload_id
    if upload_id is None:
        return
    state.upload_id = None
    try:
        await asyncio.to_thread(
            client.abort_multipart_upload,
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
        )
    except ClientError as exc:
        log_s3_error("abort_multipart_upload", exc)
    except BotoCoreError:
        _LOG.warning(
            "storage request failed",
            extra={
                "event": "s3_error",
                "operation": "abort_multipart_upload",
                "error_code": "transport",
            },
        )
