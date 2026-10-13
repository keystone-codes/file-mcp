"""Run file-mcp: load settings, wait for the bucket, serve HTTP."""

from __future__ import annotations

import uvicorn

from file_mcp.app import build_app
from file_mcp.config import load_settings
from file_mcp.logs import configure_logging
from file_mcp.s3 import make_s3_client, wait_for_bucket


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    client = make_s3_client(settings)
    wait_for_bucket(client, settings)
    uvicorn.run(
        build_app(settings, s3=client),
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
