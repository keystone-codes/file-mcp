"""File tools over the MCP HTTP entrypoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastmcp.utilities.tests import asgi_server
from moto import mock_aws
from tests.support.calls import as_dict, error_body, tool_headers
from tests.support.env import load_required

from file_mcp.app import build_mcp, transport_kwargs
from file_mcp.file_ids import new_object_key
from file_mcp.s3 import make_s3_client
from file_mcp.tokens import verify


class _NoS3:
    def __getattr__(self, name: str):
        def _refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(f"unexpected s3 call {name}")

        return _refuse


def _parse_z(value: str) -> datetime:
    assert value.endswith("Z")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _near(value: str, seconds: float) -> None:
    delta = abs((_parse_z(value) - datetime.now(timezone.utc)).total_seconds() - seconds)
    assert delta <= 2


async def _call(running, settings, tool: str, arguments: dict, **identity):
    headers = tool_headers(settings, tool, **identity)
    async with running.client(headers=headers) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


async def test_ttl_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            short = await _call(
                running,
                settings,
                "create_upload_url",
                {"link_ttl_seconds": 60, "retention_days": 7, "filename": "a.txt"},
            )
            assert short.is_error is False
            body = as_dict(short)
            _near(body["expires_at"], 60)
            _near(body["retention_until"], 7 * 86400)
            token = body["upload_url"].rsplit("/", 1)[-1]
            async with running.http_client() as http:
                stored = await http.put(
                    f"/u/{token}",
                    content=b"hello",
                    headers={"Content-Type": "text/plain"},
                )
            assert stored.status_code == 201
            key = body["file_id"].removeprefix("eph:")
            tags = client.get_object_tagging(Bucket=settings.s3_bucket, Key=key)["TagSet"]
            assert {"Key": "retention-days", "Value": "7"} in tags

            for bad in (0, settings.link_ttl_max_seconds + 1):
                refused = await _call(
                    running,
                    settings,
                    "create_upload_url",
                    {"link_ttl_seconds": bad},
                )
                err = error_body(refused)
                assert err["code"] == "invalid_argument"
                assert f"1 to {settings.link_ttl_max_seconds}" in err["message"]

            plain = await _call(running, settings, "create_upload_url", {})
            assert plain.is_error is False
            plain_body = as_dict(plain)
            _near(plain_body["expires_at"], settings.link_ttl_seconds)
            _near(plain_body["retention_until"], settings.upload_ttl_days * 86400)


async def test_create_upload_url_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, _NoS3())
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        result = await _call(running, settings, "create_upload_url", {})
    assert result.is_error is False
    body = as_dict(result)
    assert body["max_bytes"] == 104_857_600
    assert isinstance(body["upload_url"], str)
    assert isinstance(body["file_id"], str)
    assert isinstance(body["expires_at"], str)
    assert isinstance(body["retention_until"], str)
    assert body["upload_url"].startswith(settings.public_base_url + "/u/")
    assert body["file_id"].startswith("eph:uploads/")
    _near(body["expires_at"], 900)
    assert "warnings" not in body


async def test_create_upload_url_rejects_bad_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, _NoS3())
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        typed = await _call(
            running,
            settings,
            "create_upload_url",
            {"content_type": "text/html; charset=utf-8"},
        )
        assert error_body(typed)["code"] == "invalid_argument"
        long_name = await _call(
            running,
            settings,
            "create_upload_url",
            {"filename": "a" * 256},
        )
        assert error_body(long_name)["code"] == "invalid_argument"
        cleaned = await _call(
            running,
            settings,
            "create_upload_url",
            {"filename": "../../etc/passwd"},
        )
    assert cleaned.is_error is False
    body = as_dict(cleaned)
    claims = verify(
        body["upload_url"].rsplit("/", 1)[-1],
        "u",
        secret=settings.signing_secret.get_secret_value(),
    )
    assert claims["fn"] == "passwd"
    assert "." not in claims["k"].removeprefix("uploads/")


async def test_owner_gets_download_link(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            created = await _call(
                running,
                settings,
                "create_upload_url",
                {"filename": "note.txt", "content_type": "text/plain"},
            )
            upload = as_dict(created)
            token = upload["upload_url"].rsplit("/", 1)[-1]
            async with running.http_client() as http:
                stored = await http.put(
                    f"/u/{token}",
                    content=b"abc",
                    headers={"Content-Type": "text/plain"},
                )
            assert stored.status_code == 201
            link = await _call(
                running,
                settings,
                "get_download_link",
                {"file_id": upload["file_id"]},
                sub="ann@example.com",
            )
    assert link.is_error is False
    body = as_dict(link)
    assert body["file_url"].startswith(settings.public_base_url + "/d/")


async def test_other_user_gets_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            created = await _call(running, settings, "create_upload_url", {"filename": "a.bin"})
            upload = as_dict(created)
            token = upload["upload_url"].rsplit("/", 1)[-1]
            async with running.http_client() as http:
                stored = await http.put(f"/u/{token}", content=b"abc")
            assert stored.status_code == 201
            stolen = await _call(
                running,
                settings,
                "get_download_link",
                {"file_id": upload["file_id"]},
                sub="bob@example.com",
                roles=["administrator"],
            )
            missing_id = "eph:" + new_object_key(None)
            missing = await _call(
                running,
                settings,
                "get_download_link",
                {"file_id": missing_id},
                sub="bob@example.com",
                roles=["administrator"],
            )
    stolen_err = error_body(stolen)
    missing_err = error_body(missing)
    assert stolen_err["code"] == "file_not_found"
    assert missing_err["code"] == "file_not_found"
    assert stolen_err["message"] == missing_err["message"] == "file not found or not yours"


async def test_link_before_upload_is_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            created = await _call(running, settings, "create_upload_url", {})
            file_id = as_dict(created)["file_id"]
            early = await _call(
                running,
                settings,
                "get_download_link",
                {"file_id": file_id},
            )
    assert error_body(early)["code"] == "file_not_found"


async def test_bare_key_and_foreign_prefix_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, _NoS3())
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        bare = await _call(
            running,
            settings,
            "get_download_link",
            {"file_id": new_object_key("a.txt")},
        )
        foreign = await _call(
            running,
            settings,
            "get_file_info",
            {"file_id": "bvs:uploads/00000000-0000-4000-8000-000000000000"},
        )
    assert error_body(bare)["code"] == "invalid_file_id"
    assert error_body(foreign)["code"] == "invalid_file_id"


async def test_get_file_info(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    name = "отчёт 2026.xlsx"
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    payload = "таблицы".encode()
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            created = await _call(
                running,
                settings,
                "create_upload_url",
                {"filename": name, "content_type": media},
            )
            upload = as_dict(created)
            token = upload["upload_url"].rsplit("/", 1)[-1]
            async with running.http_client() as http:
                stored = await http.put(
                    f"/u/{token}",
                    content=payload,
                    headers={"Content-Type": media},
                )
            assert stored.status_code == 201
            info = await _call(
                running,
                settings,
                "get_file_info",
                {"file_id": upload["file_id"]},
            )
    assert info.is_error is False
    body = as_dict(info)
    assert body["filename"] == name
    assert body["content_type"] == media
    assert body["size"] == len(payload)
    created_at = _parse_z(body["created_at"])
    expires_at = _parse_z(body["expires_at"])
    assert expires_at - created_at == timedelta(days=30)


async def test_tool_output_schema_published(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, _NoS3())
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        async with running.client(headers=tool_headers(settings, "get_file_info")) as client:
            tools = await client.list_tools()
    found = {tool.name: tool for tool in tools}
    for name in ("create_upload_url", "get_download_link", "get_file_info"):
        schema = _output_schema(found[name])
        assert schema
        assert schema.get("type") == "object"


async def test_storage_error_maps_to_storage_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from botocore.exceptions import ClientError

    class _Boom:
        def head_object(self, **_kwargs: object) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "InternalError", "Message": "boom"},
                    "ResponseMetadata": {"HTTPStatusCode": 500},
                },
                "HeadObject",
            )

    settings = load_required(monkeypatch)
    file_id = "eph:" + new_object_key("a.txt")
    mcp = build_mcp(settings, _Boom())
    caplog.set_level("WARNING")
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        result = await _call(
            running,
            settings,
            "get_download_link",
            {"file_id": file_id},
        )
    err = error_body(result)
    assert err == {
        "code": "storage_unavailable",
        "message": "storage is temporarily unavailable",
    }
    assert "boom" not in err["message"]
    matched = [record for record in caplog.records if getattr(record, "event", "") == "s3_error"]
    assert matched
    assert matched[0].operation == "head_object"
    assert matched[0].error_code == "InternalError"


async def test_retention_warning_when_lifecycle_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, _NoS3(), lifecycle_supported=False)
    async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
        result = await _call(running, settings, "create_upload_url", {})
    assert as_dict(result)["warnings"] == ["retention_unsupported"]


def _output_schema(tool) -> dict | None:
    value = getattr(tool, "output_schema", None)
    if isinstance(value, dict):
        return value
    if hasattr(tool, "model_dump"):
        dumped = tool.model_dump()
        schema = dumped.get("output_schema")
        if isinstance(schema, dict):
            return schema
    return None
