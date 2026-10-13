"""Build a Keystone identity assertion for tests."""

from __future__ import annotations

import time
import uuid

import jwt

_TYP = "keystone-identity+jwt"


def sign_assertion(
    secret: str,
    *,
    mcp_target: str,
    aud: str = "file-mcp",
    sub: str = "ann@example.com",
    exp_in: int = 240,
    **overrides: object,
) -> str:
    """Sign one HS256 assertion. ``overrides`` replace individual claims."""
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://keystone.example",
        "aud": aud,
        "sub": sub,
        "sub_type": "human",
        "email": sub,
        "roles": ["member"],
        "rbac": "off",
        "auth_method": "dev",
        "interactive": True,
        "client_id": "test-client",
        "task_id": "task-1",
        "mcp_method": "tools/call",
        "mcp_target": mcp_target,
        "iat": now,
        "exp": now + exp_in,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    token = jwt.encode(
        claims,
        secret,
        algorithm="HS256",
        headers={"typ": _TYP},
    )
    if isinstance(token, bytes):
        return token.decode("ascii")
    return token
