"""Signed link tokens."""

from __future__ import annotations

import pytest

from file_mcp.file_ids import new_object_key
from file_mcp.tokens import InvalidToken, issue_download, issue_upload, verify

_SECRET = "s" * 32
_OTHER = "o" * 32


def _upload(**overrides: object) -> str:
    fields = {
        "key": new_object_key("report.pdf"),
        "owner": "ann@example.com",
        "exp": 1_700_000_000,
        "max_bytes": 100,
        "retention_days": 30,
        "secret": _SECRET,
        "content_type": "application/pdf",
        "filename": "report.pdf",
    }
    fields.update(overrides)
    return issue_upload(**fields)  # type: ignore[arg-type]


def test_roundtrip_upload_and_download() -> None:
    key = new_object_key("report.pdf")
    upload = issue_upload(
        key=key,
        owner="ann@example.com",
        exp=1_700_000_000,
        max_bytes=100,
        retention_days=7,
        secret=_SECRET,
        content_type="application/pdf",
        filename="report.pdf",
    )
    assert len(upload.split(".", 1)[1]) == 43
    got = verify(upload, "u", secret=_SECRET, now=1_700_000_000)
    assert got["k"] == key
    assert got["o"] == "ann@example.com"
    assert got["rd"] == 7
    download = issue_download(key=key, exp=1_700_000_000, secret=_SECRET)
    assert verify(download, "d", secret=_SECRET, now=1_700_000_000)["k"] == key


def test_kind_confusion_rejected() -> None:
    key = new_object_key("report.pdf")
    upload = _upload(key=key)
    download = issue_download(key=key, exp=1_700_000_000, secret=_SECRET)
    with pytest.raises(InvalidToken):
        verify(upload, "d", secret=_SECRET, now=1_700_000_000)
    with pytest.raises(InvalidToken):
        verify(download, "u", secret=_SECRET, now=1_700_000_000)


def test_tampered_payload_or_signature_rejected() -> None:
    token = _upload()
    payload, sig = token.split(".", 1)
    flipped_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    flipped_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(InvalidToken):
        verify(flipped_payload + "." + sig, "u", secret=_SECRET, now=1_700_000_000)
    with pytest.raises(InvalidToken):
        verify(payload + "." + flipped_sig, "u", secret=_SECRET, now=1_700_000_000)


def test_expired_rejected() -> None:
    token = _upload(exp=100)
    with pytest.raises(InvalidToken):
        verify(token, "u", secret=_SECRET, now=101)
    assert verify(token, "u", secret=_SECRET, now=100)["exp"] == 100


def test_wrong_secret_rejected() -> None:
    token = _upload()
    with pytest.raises(InvalidToken):
        verify(token, "u", secret=_OTHER, now=1_700_000_000)


def test_overlong_token_rejected_before_hmac(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("hmac was called")

    monkeypatch.setattr("file_mcp.tokens.hmac.new", boom)
    monkeypatch.setattr("file_mcp.tokens.hmac.compare_digest", boom)
    with pytest.raises(InvalidToken):
        verify("a" * 2049, "u", secret=_SECRET)


def test_unicode_filename_and_long_owner_fit_limit() -> None:
    filename = "\ua640" * 85
    owner = "a" * 254
    assert len(filename.encode("utf-8")) == 255
    content_type = ("a" * 63) + "/" + ("b" * 63)
    token = issue_upload(
        key=new_object_key(filename),
        owner=owner,
        exp=1_700_000_000,
        max_bytes=104_857_600,
        retention_days=3650,
        secret=_SECRET,
        content_type=content_type,
        filename=filename,
    )
    assert len(token) <= 2048
    got = verify(token, "u", secret=_SECRET, now=1_700_000_000)
    assert got["fn"] == filename
    assert got["o"] == owner
