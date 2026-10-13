"""Bearer and X-Keystone-Identity checks."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from pathlib import Path

import pytest
from fastmcp.utilities.tests import asgi_server
from tests.support.assertions import sign_assertion
from tests.support.env import load_required

from file_mcp._vendor import KEYSTONE_IDENTITY_SHA256
from file_mcp.app import build_mcp, transport_kwargs
from file_mcp.auth import current_identity
from file_mcp.config import Settings

_OTHER_SECRET = "w" * 32
_PREVIOUS_SECRET = "p" * 32


def _echo_mcp(settings: Settings):
    mcp = build_mcp(settings)

    @mcp.tool
    def echo_sub() -> str:
        return current_identity().sub

    return mcp


def _bearer(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.bearer_token.get_secret_value()}"}


def _assertion(settings: Settings, **overrides: object) -> str:
    secret = settings.identity_secret.get_secret_value()
    claims = {"mcp_target": "echo_sub"}
    claims.update(overrides)
    return sign_assertion(secret, **claims)  # type: ignore[arg-type]


def _error_body(result) -> dict[str, str]:
    assert result.is_error
    return json.loads(result.content[0].text)


async def test_mcp_requires_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.http_client() as http:
            missing = await http.post("/mcp")
            wrong = await http.post(
                "/mcp",
                headers={"Authorization": "Bearer " + "z" * 32},
            )
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json() == {"error": "unauthorized"}
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == "Bearer"


async def test_bearer_checked_on_every_method(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.http_client() as http:
            get_response = await http.get("/mcp")
            delete_response = await http.delete("/mcp")
    assert get_response.status_code == 401
    assert delete_response.status_code == 401
    assert get_response.headers["www-authenticate"] == "Bearer"
    assert delete_response.headers["www-authenticate"] == "Bearer"


async def test_list_tools_needs_bearer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=_bearer(settings)) as client:
            tools = await client.list_tools()
    names = [tool.name for tool in tools]
    assert "echo_sub" in names


async def test_call_without_identity_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=_bearer(settings)) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_call_with_valid_identity_sees_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = _assertion(settings)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert result.is_error is False
    assert result.data == "ann@example.com"


async def test_wrong_audience_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = _assertion(settings, aud="other-mcp")
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_wrong_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = sign_assertion(_OTHER_SECRET, mcp_target="echo_sub")
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_expired_assertion_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = _assertion(settings, exp_in=-120)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_alg_none_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)

    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    claims = {
        "iss": "https://keystone.example",
        "aud": "file-mcp",
        "sub": "ann@example.com",
        "iat": now,
        "exp": now + 240,
        "mcp_method": "tools/call",
        "mcp_target": "echo_sub",
    }
    token = f"{b64({'alg': 'none', 'typ': 'keystone-identity+jwt'})}.{b64(claims)}."
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = token
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_assertion_for_other_tool_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = _assertion(settings, mcp_target="other")
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(result)["code"] == "identity_rejected"


async def test_previous_secret_accepted_during_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch, FILE_MCP_IDENTITY_SECRET_PREVIOUS=_PREVIOUS_SECRET)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = sign_assertion(_PREVIOUS_SECRET, mcp_target="echo_sub")
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=headers) as client:
            result = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert result.is_error is False
    assert result.data == "ann@example.com"


async def test_issuer_checked_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch, FILE_MCP_IDENTITY_ISSUER="https://keystone.example")
    ok_headers = _bearer(settings)
    ok_headers["X-Keystone-Identity"] = _assertion(settings)
    bad_headers = _bearer(settings)
    bad_headers["X-Keystone-Identity"] = _assertion(settings, iss="https://other.example")
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=ok_headers) as client:
            accepted = await client.call_tool("echo_sub", {}, raise_on_error=False)
        async with running.client(headers=bad_headers) as client:
            rejected = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert accepted.data == "ann@example.com"
    assert _error_body(rejected)["code"] == "identity_rejected"


async def test_author_email_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    only_email = _bearer(settings)
    only_email["X-Author-Email"] = "boss@example.com"
    both = _bearer(settings)
    both["X-Keystone-Identity"] = _assertion(settings)
    both["X-Author-Email"] = "boss@example.com"
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.client(headers=only_email) as client:
            rejected = await client.call_tool("echo_sub", {}, raise_on_error=False)
        async with running.client(headers=both) as client:
            accepted = await client.call_tool("echo_sub", {}, raise_on_error=False)
    assert _error_body(rejected)["code"] == "identity_rejected"
    assert accepted.data == "ann@example.com"


async def test_tokens_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = load_required(monkeypatch)
    bearer = settings.bearer_token.get_secret_value()
    assertion = _assertion(settings)
    headers = _bearer(settings)
    headers["X-Keystone-Identity"] = assertion
    caplog.set_level(logging.DEBUG)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.http_client() as http:
            await http.post("/mcp")
            await http.post("/mcp", headers={"Authorization": "Bearer " + "z" * 32})
        async with running.client(headers=_bearer(settings)) as client:
            await client.list_tools()
            await client.call_tool("echo_sub", {}, raise_on_error=False)
        async with running.client(headers=headers) as client:
            await client.call_tool("echo_sub", {}, raise_on_error=False)
    logged = caplog.text + "\n".join(repr(record.__dict__) for record in caplog.records)
    assert bearer not in logged
    assert assertion not in logged


async def test_mcp_accepts_container_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    async with asgi_server(
        _echo_mcp(settings), path="/mcp", **transport_kwargs(settings)
    ) as running:
        async with running.http_client() as http:
            response = await http.post("http://file-mcp:8081/mcp", headers=_bearer(settings))
    assert response.status_code not in {401, 421}


def test_vendored_identity_module_unchanged() -> None:
    path = Path(__file__).resolve().parents[1] / "src/file_mcp/_vendor/keystone_identity.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == KEYSTONE_IDENTITY_SHA256
