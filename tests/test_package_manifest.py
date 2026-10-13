"""The catalog manifest matches the service that is actually registered."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from tests.support.env import load_required

from file_mcp import __version__
from file_mcp.app import build_mcp
from file_mcp.config import Settings

_ROOT = Path(__file__).resolve().parents[1]
_IMAGE = re.compile(r"^ghcr\.io/keystone-codes/file-mcp@sha256:[0-9a-f]{64}$")


def _manifest() -> dict:
    loaded = yaml.safe_load((_ROOT / "keystone-service.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _policy() -> dict:
    loaded = yaml.safe_load((_ROOT / "policy.example.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _field_name(env_name: str) -> str:
    assert env_name.startswith("FILE_MCP_")
    return env_name.removeprefix("FILE_MCP_").lower()


async def test_manifest_tools_match_registered_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    mcp = build_mcp(settings, object())
    listed = await mcp.list_tools()
    names = {tool.name for tool in listed}
    document = _manifest()
    tools = document["mcp"]["tools"]
    critical = document["mcp"]["critical"]
    assert set(tools) == names
    assert set(critical) <= set(tools)


def test_manifest_single_mcp_container() -> None:
    document = _manifest()
    containers = document["containers"]
    assert set(containers) == {"mcp"}
    assert document["mcp"]["container"] == "mcp"
    assert "init" not in containers["mcp"]


def test_manifest_env_names_exist_in_settings() -> None:
    document = _manifest()
    container = document["containers"]["mcp"]
    names: list[str] = []
    defaults: list[tuple[str, str]] = []
    for item in container["env"]:
        names.append(item["name"])
        defaults.append((item["name"], str(item["default"])))
    for item in container["secrets"]:
        names.append(item["name"])
    for value in document["needs"][0]["bind_env"].values():
        names.append(value)
    names.append(document["mcp"]["bearer_env"])
    names.append(document["identity"]["secret_env"])
    names.append(document["identity"]["audience_env"])
    names.append(document["public_base_url_env"])
    for env_name in names:
        assert _field_name(env_name) in Settings.model_fields
    for env_name, declared in defaults:
        default = Settings.model_fields[_field_name(env_name)].default
        assert str(default) == declared


def test_manifest_version_matches_pyproject() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _manifest()["version"] == project["project"]["version"] == __version__


def test_manifest_image_is_pinned() -> None:
    image = _manifest()["containers"]["mcp"]["image"]
    assert _IMAGE.fullmatch(image)


def test_policy_example_names_only_manifest_tools() -> None:
    tools = _manifest()["mcp"]["tools"]
    published = {f"files_{name}" for name in tools}
    allow = _policy()["roles"]["files_user"]["allow"]
    assert set(allow) <= published
    for name in _policy()["critical_tools"]:
        assert name in published


def test_release_workflows_do_not_publish_latest() -> None:
    for name in ("release-image.yml", "tag-release.yml", "test.yml"):
        text = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert ":latest" not in text
        assert "value=latest" not in text
