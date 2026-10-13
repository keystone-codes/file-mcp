"""Call one file-mcp tool on a service that is already running.

Reads ``FILE_MCP_*`` from ``.env`` in the working directory. The file must be
mode 600. Secrets are never taken from arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
import sys
import time
import uuid
from pathlib import Path

import jwt
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from file_mcp.config import load_settings

_DEV_SUB = "dev@localhost"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Call one file-mcp tool.")
    parser.add_argument(
        "tool",
        choices=("create_upload_url", "get_download_link", "get_file_info"),
    )
    parser.add_argument("--filename")
    parser.add_argument("--content-type")
    parser.add_argument("--file-id")
    parser.add_argument("--link-ttl-seconds", type=int)
    parser.add_argument("--retention-days", type=int)
    parser.add_argument("--subject", default=_DEV_SUB)
    args = parser.parse_args(argv)
    if args.tool in {"get_download_link", "get_file_info"} and not args.file_id:
        parser.error("--file-id is required")
    settings = load_settings(_read_env(Path(".env")))
    try:
        payload = asyncio.run(_call(settings, args))
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(payload, ensure_ascii=False))


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        print("config error: .env is required and must be mode 600", file=sys.stderr)
        raise SystemExit(2)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        print("config error: .env must be mode 600", file=sys.stderr)
        raise SystemExit(2)
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


async def _call(settings, args: argparse.Namespace) -> dict:
    arguments: dict[str, object] = {}
    if args.filename is not None:
        arguments["filename"] = args.filename
    if args.content_type is not None:
        arguments["content_type"] = args.content_type
    if args.file_id is not None:
        arguments["file_id"] = args.file_id
    if args.link_ttl_seconds is not None:
        arguments["link_ttl_seconds"] = args.link_ttl_seconds
    if args.retention_days is not None:
        arguments["retention_days"] = args.retention_days
    host = settings.host
    if host in {"0.0.0.0", "::", "::0"}:
        host = "127.0.0.1"
    url = f"http://{host}:{settings.port}/mcp"
    assertion = _sign_assertion(
        settings.identity_secret.get_secret_value(),
        mcp_target=args.tool,
        aud=settings.registry_name,
        sub=args.subject,
        issuer=settings.identity_issuer,
    )
    transport = StreamableHttpTransport(
        url,
        headers={"X-Keystone-Identity": assertion},
        auth=settings.bearer_token.get_secret_value(),
    )
    async with Client(transport) as client:
        result = await client.call_tool(args.tool, arguments)
    data = result.data
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    if isinstance(result.structured_content, dict):
        return result.structured_content
    raise SystemExit(1)


def _sign_assertion(
    secret: str,
    *,
    mcp_target: str,
    aud: str,
    sub: str,
    issuer: str,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer or "https://keystone.example",
        "aud": aud,
        "sub": sub,
        "sub_type": "human",
        "email": sub,
        "roles": ["member"],
        "rbac": "off",
        "auth_method": "dev",
        "interactive": True,
        "client_id": "dev-call",
        "task_id": "dev-call",
        "mcp_method": "tools/call",
        "mcp_target": mcp_target,
        "iat": now,
        "exp": now + 240,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(claims, secret, algorithm="HS256", headers={"typ": "keystone-identity+jwt"})
    if isinstance(token, bytes):
        return token.decode("ascii")
    return token


if __name__ == "__main__":
    main()
