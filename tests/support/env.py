"""Shared environment setup for file-mcp tests."""

from __future__ import annotations

import os

import pytest

from file_mcp.config import Settings, load_settings

_REQUIRED = {
    "FILE_MCP_PUBLIC_BASE_URL": "https://files.example.com",
    "FILE_MCP_BEARER_TOKEN": "b" * 32,
    "FILE_MCP_IDENTITY_SECRET": "i" * 32,
    "FILE_MCP_SIGNING_SECRET": "s" * 32,
    "FILE_MCP_S3_ENDPOINT": "https://s3.amazonaws.com",
    "FILE_MCP_S3_BUCKET": "file-mcp",
    "FILE_MCP_S3_ACCESS_KEY": "file-mcp-access",
    "FILE_MCP_S3_SECRET_KEY": "k" * 32,
}


def apply_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Replace every ``FILE_MCP_*`` variable, then set the required ones."""
    for key in list(os.environ):
        if key.startswith("FILE_MCP_"):
            monkeypatch.delenv(key, raising=False)
    values = dict(_REQUIRED)
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def load_required(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    apply_env(monkeypatch, **overrides)
    return load_settings()
