"""Opaque ``file_id`` values. Callers never pass a bare object key."""

from __future__ import annotations

import re
import uuid

_FILE_ID_RE = re.compile(
    r"^eph:uploads/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
    r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}(\.[a-z0-9]{1,16})?$"
)
_EXT_RE = re.compile(r"[a-z0-9]{1,16}")


class InvalidFileId(Exception):
    """The value is not a file id issued by this service."""


def new_object_key(filename: str | None) -> str:
    """``uploads/<uuid4>`` or ``uploads/<uuid4>.<ext>`` from the cleaned filename."""
    ext = _extension(filename)
    ident = str(uuid.uuid4())
    if ext is None:
        return f"uploads/{ident}"
    return f"uploads/{ident}.{ext}"


def parse_file_id(value: str) -> str:
    """Return the object key, or raise :class:`InvalidFileId`."""
    if not isinstance(value, str) or _FILE_ID_RE.fullmatch(value) is None:
        raise InvalidFileId()
    return value[4:]


def _extension(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    if _EXT_RE.fullmatch(ext) is None:
        return None
    return ext
