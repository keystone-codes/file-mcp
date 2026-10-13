"""Call file-mcp tools in tests."""

from __future__ import annotations

import json

from tests.support.assertions import sign_assertion

from file_mcp.config import Settings


def tool_headers(settings: Settings, tool: str, **overrides: object) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.bearer_token.get_secret_value()}"}
    headers["X-Keystone-Identity"] = sign_assertion(
        settings.identity_secret.get_secret_value(),
        mcp_target=tool,
        **overrides,
    )
    return headers


def error_body(result) -> dict[str, str]:
    assert result.is_error
    return json.loads(result.content[0].text)


def as_dict(result) -> dict:
    data = result.data
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    structured = result.structured_content
    assert isinstance(structured, dict)
    return structured
