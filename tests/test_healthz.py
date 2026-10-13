"""Health and S3 startup."""

from __future__ import annotations

import httpx2
import pytest
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws
from tests.support.env import load_required

from file_mcp.app import build_app
from file_mcp.s3 import make_s3_client, wait_for_bucket


class _Down:
    def head_bucket(self, **_kwargs: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://storage.internal:9000")


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def head_bucket(self, **_kwargs: object) -> None:
        self.calls += 1


class _Flaky:
    def __init__(self) -> None:
        self.calls = 0

    def head_bucket(self, **_kwargs: object) -> None:
        self.calls += 1
        if self.calls <= 2:
            raise EndpointConnectionError(endpoint_url="http://storage.internal:9000")


async def _get(app, path: str = "/healthz"):
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8081") as http:
        return await http.get(path)


async def test_healthz_ok_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        client.create_bucket(Bucket=settings.s3_bucket)
        app = build_app(settings, s3=client)
        response = await _get(app)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_degraded_when_s3_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    app = build_app(settings, s3=_Down())
    response = await _get(app)
    body = response.text
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "s3": "unreachable"}
    assert settings.s3_bucket not in body
    assert settings.s3_endpoint not in body


async def test_healthz_caches_for_15_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    clock = {"now": 0.0}
    counter = _Counter()
    app = build_app(settings, s3=counter, clock=lambda: clock["now"])
    first = await _get(app)
    second = await _get(app)
    clock["now"] = 16.0
    third = await _get(app)
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert counter.calls == 2


def test_startup_refuses_missing_bucket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = make_s3_client(settings)
        with pytest.raises(SystemExit) as exc:
            wait_for_bucket(client, settings)
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "installer" in err
    assert settings.s3_bucket in err


def test_startup_waits_for_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("file_mcp.s3.time.sleep", lambda _seconds: None)
    settings = load_required(monkeypatch)
    flaky = _Flaky()
    wait_for_bucket(flaky, settings)
    assert flaky.calls == 3

    impatient = load_required(monkeypatch, FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS="0")
    with pytest.raises(SystemExit) as exc:
        wait_for_bucket(_Down(), impatient)
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "unreachable" in err
    assert impatient.s3_endpoint in err
