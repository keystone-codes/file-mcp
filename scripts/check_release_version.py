#!/usr/bin/env python3
"""Refuse a release version that does not match the tree or is already tagged."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_MANIFEST_VERSION = re.compile(r'(?m)^version:\s*["\']?([^"\'#\s]+)')


def version_problem(requested: str, project: str, manifest: str) -> str | None:
    """Return an error when ``requested`` is not the version of this tree."""
    if _SEMVER.fullmatch(requested) is None:
        return f"version {requested!r} is not semver X.Y.Z"
    if project != requested or manifest != requested:
        return (
            f"version {requested} does not match pyproject.toml ({project}) "
            f"and keystone-service.yaml ({manifest})"
        )
    return None


def manifest_version(text: str) -> str:
    found = _MANIFEST_VERSION.search(text)
    return found.group(1) if found else ""


def tag_exists(version: str) -> bool:
    remote = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/v{version}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if remote.returncode != 0:
        raise SystemExit(remote.stderr.strip() or "git ls-remote failed")
    return bool(remote.stdout.strip())


def main() -> int:
    requested = os.environ.get("RELEASE_VERSION", "")
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    manifest = manifest_version(Path("keystone-service.yaml").read_text(encoding="utf-8"))
    problem = version_problem(requested, project, manifest)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    if tag_exists(requested):
        print(f"tag v{requested} already exists", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
