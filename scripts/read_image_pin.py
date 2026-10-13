#!/usr/bin/env python3
"""Print the manifest version and image digest for the tag workflow."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION = re.compile(r'(?m)^version:\s*["\']?([^"\'#\s]+)')
_IMAGE = re.compile(
    r"(?m)^[ \t]*image:[ \t]+[\"']?"
    r"ghcr\.io/keystone-codes/file-mcp"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    r"@(?P<digest>sha256:[0-9a-f]{64})"
)


def read_pin(text: str) -> tuple[str, str]:
    """Return ``(version, sha256:...)`` from a manifest."""
    version = _VERSION.search(text)
    images = list(_IMAGE.finditer(text))
    if version is None or len(images) != 1:
        raise ValueError("keystone-service.yaml has no version or not exactly one pinned image")
    return version.group(1), images[0].group("digest")


def main() -> int:
    try:
        version, digest = read_pin(Path("keystone-service.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"read_image_pin: {exc}", file=sys.stderr)
        return 1
    print(f"version={version}")
    print(f"digest={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
