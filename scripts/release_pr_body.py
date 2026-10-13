#!/usr/bin/env python3
"""Pull-request text for the digest pin. No secret values."""

from __future__ import annotations

import os


def main() -> int:
    version = os.environ["RELEASE_VERSION"]
    digest = os.environ["IMAGE_DIGEST"]
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "keystone-codes/file-mcp")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    print(
        "\n".join(
            [
                "## Summary",
                "",
                f"- Version {version}",
                f"- Image digest `{digest}` (the multi-platform index, not one platform)",
                f"- Workflow run: {server}/{repository}/actions/runs/{run_id}",
                "",
                "After this pull request is merged, `tag-release.yml` tags "
                f"`v{version}` only when `docker buildx imagetools inspect` "
                "reports this same digest.",
                "",
                "Make the GHCR package `file-mcp` public in the organization "
                "package settings after the first publish. A clean machine "
                "cannot pull a private package.",
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
