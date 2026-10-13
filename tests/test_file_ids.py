"""file_id shape."""

from __future__ import annotations

import pytest

from file_mcp.file_ids import InvalidFileId, new_object_key, parse_file_id


def test_new_key_shape() -> None:
    xlsx = new_object_key("report.XLSX")
    assert xlsx.startswith("uploads/")
    assert xlsx.endswith(".xlsx")
    assert parse_file_id("eph:" + xlsx) == xlsx

    for name in ("noext", "a.tar.gz-long-suffix-over-16", "x.пдф"):
        key = new_object_key(name)
        assert key.startswith("uploads/")
        assert "." not in key.removeprefix("uploads/")
        assert parse_file_id("eph:" + key) == key


def test_parse_accepts_own_ids_only() -> None:
    own = "eph:" + new_object_key("notes.txt")
    assert parse_file_id(own) == own[4:]
    bare = own[4:]
    for value in (
        bare,
        "bvs:uploads/00000000-0000-4000-8000-000000000000",
        "eph:snapshots/00000000-0000-4000-8000-000000000000",
        "eph:uploads/../x",
        "eph:",
        "",
    ):
        with pytest.raises(InvalidFileId):
            parse_file_id(value)
