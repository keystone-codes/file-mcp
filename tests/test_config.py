"""Settings parsing."""

from __future__ import annotations

import logging

import pytest
from tests.support.env import apply_env, load_required

from file_mcp.config import load_settings

_EIGHT = (
    "FILE_MCP_PUBLIC_BASE_URL",
    "FILE_MCP_BEARER_TOKEN",
    "FILE_MCP_IDENTITY_SECRET",
    "FILE_MCP_SIGNING_SECRET",
    "FILE_MCP_S3_ENDPOINT",
    "FILE_MCP_S3_BUCKET",
    "FILE_MCP_S3_ACCESS_KEY",
    "FILE_MCP_S3_SECRET_KEY",
)


def _stderr(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    assert settings.port == 8081
    assert settings.link_ttl_seconds == 900
    assert settings.upload_ttl_days == 30
    assert settings.max_bytes == 104_857_600
    assert settings.s3_path_style is True
    assert settings.s3_region == "us-east-1"
    assert settings.registry_name == "file-mcp"


def test_missing_required_lists_every_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apply_env(monkeypatch)
    for name in _EIGHT:
        monkeypatch.delenv(name)
    with pytest.raises(SystemExit) as exc:
        load_settings()
    assert exc.value.code == 2
    err = _stderr(capsys)
    for name in _EIGHT:
        assert name in err


def test_short_secrets_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    short = "x" * 31
    for name in (
        "FILE_MCP_BEARER_TOKEN",
        "FILE_MCP_IDENTITY_SECRET",
        "FILE_MCP_IDENTITY_SECRET_PREVIOUS",
        "FILE_MCP_SIGNING_SECRET",
    ):
        apply_env(monkeypatch, **{name: short})
        with pytest.raises(SystemExit) as exc:
            load_settings()
        assert exc.value.code == 2
        assert name in _stderr(capsys)


def test_link_ttl_bounds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad in ("59", "86401"):
        apply_env(monkeypatch, FILE_MCP_LINK_TTL_SECONDS=bad)
        with pytest.raises(SystemExit) as exc:
            load_settings()
        assert exc.value.code == 2
        assert "FILE_MCP_LINK_TTL_SECONDS" in _stderr(capsys)
    assert load_required(monkeypatch, FILE_MCP_LINK_TTL_SECONDS="60").link_ttl_seconds == 60
    assert load_required(monkeypatch, FILE_MCP_LINK_TTL_SECONDS="86400").link_ttl_seconds == 86400


def test_upload_ttl_and_max_bytes_bounds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, bad in (
        ("FILE_MCP_UPLOAD_TTL_DAYS", "0"),
        ("FILE_MCP_UPLOAD_TTL_DAYS", "3651"),
        ("FILE_MCP_MAX_BYTES", "0"),
        ("FILE_MCP_MAX_BYTES", "5368709121"),
    ):
        apply_env(monkeypatch, **{name: bad})
        with pytest.raises(SystemExit) as exc:
            load_settings()
        assert exc.value.code == 2
        assert name in _stderr(capsys)
    assert load_required(monkeypatch, FILE_MCP_UPLOAD_TTL_DAYS="1").upload_ttl_days == 1
    assert load_required(monkeypatch, FILE_MCP_UPLOAD_TTL_DAYS="3650").upload_ttl_days == 3650
    assert load_required(monkeypatch, FILE_MCP_MAX_BYTES="1").max_bytes == 1
    assert load_required(monkeypatch, FILE_MCP_MAX_BYTES="5368709120").max_bytes == 5_368_709_120


def test_public_base_url_rules(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = load_required(monkeypatch, FILE_MCP_PUBLIC_BASE_URL="https://files.example.com/")
    assert settings.public_base_url == "https://files.example.com"

    for bad in ("https://x/path", "ftp://x", "https://x?a=1"):
        apply_env(monkeypatch, FILE_MCP_PUBLIC_BASE_URL=bad)
        with pytest.raises(SystemExit) as exc:
            load_settings()
        assert exc.value.code == 2
        assert "FILE_MCP_PUBLIC_BASE_URL" in _stderr(capsys)

    with caplog.at_level(logging.WARNING, logger="file_mcp.config"):
        accepted = load_required(monkeypatch, FILE_MCP_PUBLIC_BASE_URL="http://files.example.com")
    assert accepted.public_base_url == "http://files.example.com"
    assert any("non-loopback" in record.message for record in caplog.records)


def test_secrets_never_in_repr_or_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinels = {
        "FILE_MCP_BEARER_TOKEN": "SENTINEL-BEARER-VALUE-0123456789abcd",
        "FILE_MCP_IDENTITY_SECRET": "SENTINEL-IDENTITY-VALUE-0123456789ab",
        "FILE_MCP_IDENTITY_SECRET_PREVIOUS": "SENTINEL-PREVIOUS-VALUE-0123456789ab",
        "FILE_MCP_SIGNING_SECRET": "SENTINEL-SIGNING-VALUE-0123456789abcd",
        "FILE_MCP_S3_ACCESS_KEY": "SENTINEL-ACCESS-KEY-0123456789abcdef",
        "FILE_MCP_S3_SECRET_KEY": "SENTINEL-SECRET-KEY-0123456789abcdef",
    }
    settings = load_required(monkeypatch, **sentinels)
    rendered = repr(settings)
    for value in sentinels.values():
        assert value not in rendered

    apply_env(monkeypatch, **sentinels, FILE_MCP_LINK_TTL_SECONDS="1")
    with pytest.raises(SystemExit):
        load_settings()
    err = _stderr(capsys)
    for value in sentinels.values():
        assert value not in err
