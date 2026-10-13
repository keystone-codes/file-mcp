"""File links against a real MinIO bucket."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from fastmcp.utilities.tests import asgi_server
from tests.support.calls import as_dict, tool_headers

from file_mcp.app import build_mcp, transport_kwargs
from file_mcp.config import Settings
from file_mcp.lifecycle import ensure_upload_expiry
from file_mcp.s3 import make_s3_client

pytestmark = pytest.mark.integration


def _settings(monkeypatch: pytest.MonkeyPatch, minio: dict) -> Settings:
    from tests.support.env import load_required

    return load_required(
        monkeypatch,
        FILE_MCP_S3_ENDPOINT=minio["endpoint"],
        FILE_MCP_S3_BUCKET=minio["bucket"],
        FILE_MCP_S3_ACCESS_KEY=minio["access_key"],
        FILE_MCP_S3_SECRET_KEY=minio["secret_key"],
        FILE_MCP_S3_PATH_STYLE="true",
        FILE_MCP_PUBLIC_BASE_URL="http://127.0.0.1:8081",
    )


async def test_multipart_upload_100_mib(monkeypatch: pytest.MonkeyPatch, minio: dict) -> None:
    settings = _settings(monkeypatch, minio)
    client = make_s3_client(settings)
    mcp = build_mcp(settings, client, lifecycle_supported=True)
    chunk = b"m" * (1024 * 1024)
    remaining = settings.max_bytes

    async def body():
        nonlocal remaining
        while remaining:
            piece = chunk if remaining >= len(chunk) else chunk[:remaining]
            remaining -= len(piece)
            yield piece

    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        headers = tool_headers(settings, "create_upload_url")
        async with running.client(headers=headers) as caller:
            created = await caller.call_tool("create_upload_url", {}, raise_on_error=False)
        upload = as_dict(created)
        token = upload["upload_url"].rsplit("/", 1)[-1]
        async with running.http_client() as http:
            stored = await http.put("/u/" + token, content=body())
            assert stored.status_code == 201
            assert stored.json()["size"] == settings.max_bytes
            headers = tool_headers(settings, "create_upload_url")
            async with running.client(headers=headers) as caller:
                created = await caller.call_tool("create_upload_url", {}, raise_on_error=False)
            token = as_dict(created)["upload_url"].rsplit("/", 1)[-1]
            refused = await http.put(
                "/u/" + token,
                content=b"x",
                headers={"Content-Length": str(settings.max_bytes + 1)},
            )
    assert stored.status_code == 201
    assert refused.status_code == 413


async def test_lifecycle_rule_visible_in_minio(
    monkeypatch: pytest.MonkeyPatch, minio: dict
) -> None:
    settings = _settings(monkeypatch, minio)
    client = make_s3_client(settings)
    assert ensure_upload_expiry(client, settings.s3_bucket, settings.upload_ttl_days) is True
    rules = client.get_bucket_lifecycle_configuration(Bucket=settings.s3_bucket)["Rules"]
    ours = next(rule for rule in rules if rule["ID"] == "file-mcp-uploads-expiry")
    assert ours["Expiration"]["Days"] == settings.upload_ttl_days
    assert ours["Filter"]["Prefix"] == "uploads/"


async def test_metadata_roundtrip_unicode(monkeypatch: pytest.MonkeyPatch, minio: dict) -> None:
    settings = _settings(monkeypatch, minio)
    client = make_s3_client(settings)
    name = "отчёт 2026.xlsx"
    mcp = build_mcp(settings, client)
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        headers = tool_headers(settings, "create_upload_url")
        async with running.client(headers=headers) as caller:
            created = await caller.call_tool(
                "create_upload_url",
                {"filename": name, "content_type": "application/pdf"},
                raise_on_error=False,
            )
        upload = as_dict(created)
        token = upload["upload_url"].rsplit("/", 1)[-1]
        async with running.http_client() as http:
            stored = await http.put(
                "/u/" + token,
                content="содержимое".encode(),
                headers={"Content-Type": "application/pdf"},
            )
        assert stored.status_code == 201
        info_headers = tool_headers(settings, "get_file_info")
        async with running.client(headers=info_headers) as caller:
            info = await caller.call_tool(
                "get_file_info",
                {"file_id": upload["file_id"]},
                raise_on_error=False,
            )
    body = as_dict(info)
    assert body["filename"] == name
    assert unquote(body["filename"]) == name
