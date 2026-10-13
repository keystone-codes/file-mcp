#!/usr/bin/env python3
"""Replace the single image digest in keystone-service.yaml.

The rest of the file, including comments, is left as it is. The script
exits with status 1 when the file does not contain exactly one ``image:``
line, so a template replacement cannot quietly edit the wrong place.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_LINE = re.compile(r"^[ \t]*image:[ \t]+\S")
_PINNED = re.compile(r"@sha256:[0-9a-f]{64}")
_MANIFEST = Path("keystone-service.yaml")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: pin_digest.py sha256:<64 hex digits>", file=sys.stderr)
        return 2
    digest = args[0]
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:") :]
    if _DIGEST.fullmatch(digest) is None:
        print("pin_digest: digest must be 64 lowercase hex digits", file=sys.stderr)
        return 2
    try:
        text = _MANIFEST.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"pin_digest: {_MANIFEST}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    lines = text.splitlines(keepends=True)
    indexes = [index for index, line in enumerate(lines) if _IMAGE_LINE.match(line)]
    if len(indexes) != 1:
        print(
            f"pin_digest: expected exactly one image: line, found {len(indexes)}",
            file=sys.stderr,
        )
        return 1
    index = indexes[0]
    updated, count = _PINNED.subn(f"@sha256:{digest}", lines[index], count=1)
    if count != 1:
        print("pin_digest: the image line has no sha256 digest", file=sys.stderr)
        return 1
    lines[index] = updated
    _MANIFEST.write_text("".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
