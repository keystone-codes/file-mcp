"""Signed public upload and download paths."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

import pytest
from fastmcp.utilities.tests import asgi_server
from moto import mock_aws
from tests.support.calls import as_dict, tool_headers
from tests.support.env import load_required

from file_mcp import uploads
from file_mcp.app import build_mcp, transport_kwargs
from file_mcp.s3 import make_s3_client
from file_mcp.tokens import issue_download, issue_upload


class _Counting:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return wrapped


def _bucket(settings):
    client = make_s3_client(settings)
    client.create_bucket(Bucket=settings.s3_bucket)
    return client


async def _upload_url(running, settings, **arguments) -> str:
    headers = tool_headers(settings, "create_upload_url")
    async with running.client(headers=headers) as client:
        result = await client.call_tool("create_upload_url", arguments, raise_on_error=False)
    assert result.is_error is False
    return as_dict(result)["upload_url"]


def _token(url: str) -> str:
    return url.rsplit("/", 1)[-1]


async def _raw(app, method: str, path: str, headers: list[tuple[str, str]], chunks: list[bytes]):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"127.0.0.1")]
        + [(key.lower().encode(), value.encode()) for key, value in headers],
        "client": ("127.0.0.1", 9),
        "server": ("127.0.0.1", 8081),
        "root_path": "",
    }
    index = 0

    async def receive():
        nonlocal index
        if not chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        if index >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = chunks[index]
        index += 1
        return {"type": "http.request", "body": chunk, "more_body": index < len(chunks)}

    status = 500
    body = bytearray()

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")

    await app(scope, receive, send)
    return status, bytes(body)


async def test_put_then_get_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    payload = bytes((index * 17) % 256 for index in range(20 * 1024 * 1024))
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(
                running,
                settings,
                filename="отчёт 2026.xlsx",
                content_type="application/pdf",
            )
            async with running.http_client() as http:
                stored = await http.put(
                    "/u/" + _token(url),
                    content=payload,
                    headers={"Content-Type": "application/pdf"},
                )
                assert stored.status_code == 201
                body = stored.json()
                assert body["size"] == len(payload)
                assert body["content_type"] == "application/pdf"
                headers = tool_headers(settings, "get_download_link")
                async with running.client(headers=headers) as caller:
                    link = await caller.call_tool(
                        "get_download_link",
                        {"file_id": body["file_id"]},
                        raise_on_error=False,
                    )
                file_url = as_dict(link)["file_url"]
                downloaded = await http.get("/d/" + _token(file_url))
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["referrer-policy"] == "no-referrer"
    assert downloaded.headers["cache-control"] == "private, no-store"
    disposition = downloaded.headers["content-disposition"]
    assert "filename*=UTF-8''" + quote("отчёт 2026.xlsx", safe="") in disposition
    assert 'filename="' in disposition
    assert downloaded.headers["content-type"].startswith("application/pdf")
    assert downloaded.headers["content-length"] == str(len(payload))


async def test_put_streams_in_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    payload = b"q" * (20 * 1024 * 1024)
    with mock_aws():
        inner = _bucket(settings)
        client = _Counting(inner)
        mcp = build_mcp(settings, client)
        uploads.reset_peak_buffer()
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, content_type="application/octet-stream")
            async with running.http_client() as http:
                stored = await http.put(
                    "/u/" + _token(url),
                    content=payload,
                    headers={"Content-Type": "application/octet-stream"},
                )
    assert stored.status_code == 201
    assert client.calls.count("upload_part") == 3
    assert 0 < uploads.peak_buffer_bytes <= uploads.PART_SIZE


async def test_put_over_limit_by_header(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch, FILE_MCP_MAX_BYTES="1024")
    with mock_aws():
        inner = _bucket(settings)
        client = _Counting(inner)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings)
            token = _token(url)
            app = running.app
            status, body = await _raw(
                app,
                "PUT",
                f"/u/{token}",
                [("content-type", "application/octet-stream"), ("content-length", "1025")],
                [b"x"],
            )
    assert status == 413
    assert b"payload_too_large" in body
    assert "put_object" not in client.calls
    assert "upload_part" not in client.calls
    assert "create_multipart_upload" not in client.calls


async def test_put_over_limit_by_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = uploads.PART_SIZE + 64
    settings = load_required(monkeypatch, FILE_MCP_MAX_BYTES=str(limit))
    with mock_aws():
        inner = _bucket(settings)
        client = _Counting(inner)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings)
            token = _token(url)
            status, body = await _raw(
                running.app,
                "PUT",
                f"/u/{token}",
                [("content-type", "application/octet-stream")],
                [b"x" * (limit + 1)],
            )
            key = verify_key(settings, token)
            with pytest.raises(Exception):
                inner.head_object(Bucket=settings.s3_bucket, Key=key)
    assert status == 413
    assert b"payload_too_large" in body
    assert "abort_multipart_upload" in client.calls


def verify_key(settings, token: str) -> str:
    from file_mcp.tokens import verify

    return verify(token, "u", secret=settings.signing_secret.get_secret_value())["k"]


async def test_put_twice_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, content_type="text/plain")
            path = "/u/" + _token(url)
            async with running.http_client() as http:
                first = await http.put(path, content=b"one", headers={"Content-Type": "text/plain"})
                second = await http.put(
                    path, content=b"two", headers={"Content-Type": "text/plain"}
                )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"error": "already_uploaded"}


async def test_put_content_type_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, content_type="text/plain")
            path = "/u/" + _token(url)
            async with running.http_client() as http:
                mismatch = await http.put(
                    path, content=b"x", headers={"Content-Type": "application/pdf"}
                )
                accepted = await http.put(
                    path,
                    content=b"x",
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                )
    assert mismatch.status_code == 415
    assert mismatch.json() == {"error": "unsupported_content_type"}
    assert accepted.status_code == 201


async def test_put_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings)
            async with running.http_client() as http:
                empty = await http.put("/u/" + _token(url), content=b"")
    assert empty.status_code == 422
    assert empty.json() == {"error": "empty_body"}


async def test_bad_tokens_are_404(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    key = "uploads/12345678-1234-4123-8123-123456789abc"
    secret = settings.signing_secret.get_secret_value()
    now = int(time.time())
    expired = issue_upload(
        key=key,
        owner="ann@example.com",
        exp=now - 120,
        max_bytes=100,
        retention_days=1,
        secret=secret,
    )
    download = issue_download(key=key, exp=now + 60, secret=secret)
    good = issue_upload(
        key=key,
        owner="ann@example.com",
        exp=now + 60,
        max_bytes=100,
        retention_days=1,
        secret=secret,
    )
    payload, sig = good.split(".", 1)
    tampered = ("A" if payload[0] != "A" else "B") + payload[1:] + "." + sig
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            async with running.http_client() as http:
                bodies = []
                for token in (tampered, expired, download):
                    response = await http.put(f"/u/{token}", content=b"abc")
                    bodies.append((response.status_code, response.json()))
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0] == (404, {"error": "not_found"})


async def test_owner_metadata_written(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, filename="note.txt")
            token = _token(url)
            async with running.http_client() as http:
                stored = await http.put("/u/" + token, content=b"abc")
            assert stored.status_code == 201
            key = verify_key(settings, token)
            meta = client.head_object(Bucket=settings.s3_bucket, Key=key)["Metadata"]
    assert meta["owner"] == quote("ann@example.com", safe="")
    assert meta["filename"] == quote("note.txt", safe="")


async def test_public_paths_need_no_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, content_type="text/plain")
            async with running.http_client() as http:
                stored = await http.put(
                    "/u/" + _token(url),
                    content=b"abc",
                    headers={"Content-Type": "text/plain"},
                )
                assert "authorization" not in {k.lower() for k in stored.request.headers}
                file_id = stored.json()["file_id"]
                headers = tool_headers(settings, "get_download_link")
                async with running.client(headers=headers) as caller:
                    link = await caller.call_tool(
                        "get_download_link",
                        {"file_id": file_id},
                        raise_on_error=False,
                    )
                downloaded = await http.get("/d/" + _token(as_dict(link)["file_url"]))
    assert stored.status_code == 201
    assert downloaded.status_code == 200
    assert downloaded.content == b"abc"


async def test_wrong_method_405(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings)
            token = _token(url)
            async with running.http_client() as http:
                get_upload = await http.get("/u/" + token)
                put_download = await http.put("/d/" + token, content=b"no")
    assert get_upload.status_code == 405
    assert put_download.status_code == 405


async def test_tokens_absent_from_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = load_required(monkeypatch)
    caplog.set_level(logging.DEBUG)
    with mock_aws():
        client = _bucket(settings)
        mcp = build_mcp(settings, client)
        async with asgi_server(mcp, path="/mcp", **transport_kwargs(settings)) as running:
            url = await _upload_url(running, settings, filename="a.txt", content_type="text/plain")
            upload_token = _token(url)
            async with running.http_client() as http:
                stored = await http.put(
                    "/u/" + upload_token,
                    content=b"abc",
                    headers={"Content-Type": "text/plain"},
                )
                file_id = stored.json()["file_id"]
                headers = tool_headers(settings, "get_download_link")
                async with running.client(headers=headers) as caller:
                    link = await caller.call_tool(
                        "get_download_link",
                        {"file_id": file_id},
                        raise_on_error=False,
                    )
                download_token = _token(as_dict(link)["file_url"])
                await http.get("/d/" + download_token)
                await http.put("/u/not-a-token", content=b"x")
    service_records = [record for record in caplog.records if record.name.startswith("file_mcp")]
    logged = "\n".join(
        record.getMessage() + "\n" + repr(record.__dict__) for record in service_records
    )
    assert upload_token not in logged
    assert download_token not in logged
