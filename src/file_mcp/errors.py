"""Tool errors returned to the MCP client as ``isError`` JSON text."""

from __future__ import annotations

import json

from fastmcp.exceptions import ToolError


def tool_error(code: str, message: str) -> ToolError:
    """Build a tool error whose text is JSON with ``code`` and ``message``."""
    return ToolError(json.dumps({"code": code, "message": message}))
