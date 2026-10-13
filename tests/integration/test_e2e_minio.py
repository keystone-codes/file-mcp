"""An agent uploads through a real server and downloads the same bytes."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from tests.support.calls import as_dict, error_body, tool_headers
from tests.support.env import load_required
from tests.support.live_server import bind_loopback, serve

from file_mcp.app import build_app
from file_mcp.lifecycle import ensure_upload_expiry
from file_mcp.s3 import make_s3_client

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_TWELVE_MIB = 12 * 1024 * 1024


def _redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


async def _call(base: str, settings, tool: str, arguments: dict, **identity):
    transport = StreamableHttpTransport(
        f"{base}/mcp",
        headers=tool_headers(settings, tool, **identity),
    )
    async with Client(transport) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def _app(monkeypatch: pytest.MonkeyPatch, minio: dict, base: str, **overrides: str):
    settings = load_required(
        monkeypatch,
        FILE_MCP_PUBLIC_BASE_URL=base,
        FILE_MCP_S3_ENDPOINT=minio["endpoint"],
        FILE_MCP_S3_BUCKET=minio["bucket"],
        FILE_MCP_S3_ACCESS_KEY=minio["access_key"],
        FILE_MCP_S3_SECRET_KEY=minio["secret_key"],
        FILE_MCP_S3_PATH_STYLE="true",
        **overrides,
    )
    client = make_s3_client(settings)
    lifecycle = ensure_upload_expiry(client, settings.s3_bucket, settings.upload_ttl_days)
    return settings, build_app(settings, client, lifecycle_supported=lifecycle)


async def test_agent_roundtrip(monkeypatch: pytest.MonkeyPatch, minio: dict) -> None:
    sock = bind_loopback()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings, app = _app(monkeypatch, minio, base)
    payload = os.urandom(_TWELVE_MIB)
    expected = hashlib.sha256(payload).hexdigest()
    async with serve(app, sock) as served:
        created = await _call(
            served,
            settings,
            "create_upload_url",
            {"filename": "data.bin", "content_type": "application/octet-stream"},
            sub="ann@example.com",
        )
        assert created.is_error is False
        upload = as_dict(created)
        async with httpx2.AsyncClient(timeout=60) as http:
            stored = await http.put(
                upload["upload_url"],
                content=payload,
                headers={"Content-Type": "application/octet-stream"},
            )
        assert stored.status_code == 201
        info = await _call(
            served,
            settings,
            "get_file_info",
            {"file_id": upload["file_id"]},
            sub="ann@example.com",
        )
        assert info.is_error is False
        body = as_dict(info)
        assert body["size"] == _TWELVE_MIB
        assert body["filename"] == "data.bin"
        link = await _call(
            served,
            settings,
            "get_download_link",
            {"file_id": upload["file_id"]},
            sub="ann@example.com",
        )
        assert link.is_error is False
        async with httpx2.AsyncClient(timeout=60) as http:
            downloaded = await http.get(as_dict(link)["file_url"])
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == expected
        assert "data.bin" in downloaded.headers["content-disposition"]


async def test_other_subject_gets_file_not_found(
    monkeypatch: pytest.MonkeyPatch, minio: dict
) -> None:
    sock = bind_loopback()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings, app = _app(monkeypatch, minio, base)
    async with serve(app, sock) as served:
        created = await _call(
            served,
            settings,
            "create_upload_url",
            {"filename": "data.bin", "content_type": "application/octet-stream"},
            sub="ann@example.com",
        )
        upload = as_dict(created)
        async with httpx2.AsyncClient(timeout=30) as http:
            stored = await http.put(
                upload["upload_url"],
                content=b"owned-by-ann",
                headers={"Content-Type": "application/octet-stream"},
            )
        assert stored.status_code == 201
        for tool in ("get_download_link", "get_file_info"):
            refused = await _call(
                served,
                settings,
                tool,
                {"file_id": upload["file_id"]},
                sub="bob@example.com",
                email="bob@example.com",
                roles=["administrator"],
            )
            assert error_body(refused)["code"] == "file_not_found"


async def test_expired_download_link_is_404(monkeypatch: pytest.MonkeyPatch, minio: dict) -> None:
    sock = bind_loopback()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings, app = _app(monkeypatch, minio, base, FILE_MCP_LINK_TTL_SECONDS="60")
    async with serve(app, sock) as served:
        created = await _call(
            served,
            settings,
            "create_upload_url",
            {"content_type": "application/octet-stream"},
            sub="ann@example.com",
        )
        upload = as_dict(created)
        async with httpx2.AsyncClient(timeout=30) as http:
            stored = await http.put(
                upload["upload_url"],
                content=b"short-lived",
                headers={"Content-Type": "application/octet-stream"},
            )
            assert stored.status_code == 201
            link = await _call(
                served,
                settings,
                "get_download_link",
                {"file_id": upload["file_id"]},
                sub="ann@example.com",
            )
            expires = datetime.strptime(as_dict(link)["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            # TTL is 60 seconds, so one second past expires_at is 61 seconds on.
            monkeypatch.setattr(
                "file_mcp.tokens.time.time",
                lambda: expires.timestamp() + 1,
            )
            downloaded = await http.get(as_dict(link)["file_url"])
        assert downloaded.status_code == 404


async def test_discovery_needs_bearer_only(monkeypatch: pytest.MonkeyPatch, minio: dict) -> None:
    sock = bind_loopback()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings, app = _app(monkeypatch, minio, base)
    async with serve(app, sock) as served:
        transport = StreamableHttpTransport(
            f"{served}/mcp",
            headers={"Authorization": f"Bearer {settings.bearer_token.get_secret_value()}"},
        )
        async with Client(transport) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {"create_upload_url", "get_download_link", "get_file_info"}
        async with httpx2.AsyncClient() as http:
            denied = await http.post(f"{served}/mcp", content=b"{}")
        assert denied.status_code == 401


def test_container_healthcheck(minio: dict, tmp_path: Path) -> None:
    built = subprocess.run(
        ["docker", "build", "-q", str(_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.fail(built.stderr[-2000:] or "docker build failed")
    image_lines = [line for line in built.stdout.splitlines() if line.strip()]
    if not image_lines:
        pytest.fail("docker build did not print an image id")
    image = image_lines[-1].strip()
    network = f"ks-pkg-file-mcp-f03-{os.getpid()}"
    container = f"ks-cat-file-mcp-f03-{os.getpid()}"
    bearer = "b" * 32
    identity = "i" * 32
    signing = "s" * 32
    secrets = [minio["access_key"], minio["secret_key"], bearer, identity, signing]
    created = subprocess.run(
        ["docker", "network", "create", network],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.fail(_redact(created.stderr, secrets) or "docker network create failed")
    connected = subprocess.run(
        ["docker", "network", "connect", network, minio["name"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if connected.returncode != 0:
        subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)
        pytest.fail(_redact(connected.stderr, secrets) or "docker network connect failed")
    env_file = tmp_path / "service.env"
    env_file.write_text(
        "\n".join(
            [
                "FILE_MCP_HOST=0.0.0.0",
                "FILE_MCP_PORT=8081",
                "FILE_MCP_PUBLIC_BASE_URL=http://127.0.0.1:8081",
                f"FILE_MCP_S3_ENDPOINT=http://{minio['name']}:9000",
                f"FILE_MCP_S3_BUCKET={minio['bucket']}",
                f"FILE_MCP_S3_ACCESS_KEY={minio['access_key']}",
                f"FILE_MCP_S3_SECRET_KEY={minio['secret_key']}",
                "FILE_MCP_S3_PATH_STYLE=true",
                "FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS=20",
                f"FILE_MCP_BEARER_TOKEN={bearer}",
                f"FILE_MCP_IDENTITY_SECRET={identity}",
                f"FILE_MCP_SIGNING_SECRET={signing}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--network",
            network,
            "--env-file",
            str(env_file),
            image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        if started.returncode != 0:
            pytest.fail(_redact(started.stderr, secrets) or "docker run failed")
        deadline = time.monotonic() + 60
        status = ""
        while time.monotonic() < deadline:
            inspected = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}} {{.State.Health.Status}}",
                    container,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            state, _, health = inspected.stdout.strip().partition(" ")
            status = health or state
            if health == "healthy":
                return
            if state == "exited" or health == "unhealthy":
                break
            time.sleep(2)
        pytest.fail(f"container health is {status or 'unknown'}")
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)
        subprocess.run(["docker", "container", "rm", container], capture_output=True, check=False)
        subprocess.run(
            ["docker", "network", "disconnect", network, minio["name"]],
            capture_output=True,
            check=False,
        )
        subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)
