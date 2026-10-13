"""pin_digest.py edits one image line and leaves the rest of the file alone."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pin_digest.py"
_OLD = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_NEW = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
_SAMPLE = f"""\
# keep this comment
schema_version: 1
containers:
  mcp:
    image: ghcr.io/keystone-codes/file-mcp@sha256:{_OLD} # stay
"""


def _run(tmp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pin_replaces_only_the_digest(tmp_path: Path) -> None:
    manifest = tmp_path / "keystone-service.yaml"
    manifest.write_text(_SAMPLE, encoding="utf-8")
    completed = _run(tmp_path, f"sha256:{_NEW}")
    assert completed.returncode == 0
    text = manifest.read_text(encoding="utf-8")
    assert f"@sha256:{_NEW}" in text
    assert _OLD not in text
    assert "# keep this comment" in text
    assert "# stay" in text
    assert text.startswith("# keep this comment\n")


def test_pin_fails_unless_there_is_one_image_line(tmp_path: Path) -> None:
    manifest = tmp_path / "keystone-service.yaml"
    manifest.write_text("name: file-mcp\n", encoding="utf-8")
    missing = _run(tmp_path, _NEW)
    assert missing.returncode == 1
    assert "exactly one" in missing.stderr

    manifest.write_text(_SAMPLE + _SAMPLE, encoding="utf-8")
    doubled = _run(tmp_path, _NEW)
    assert doubled.returncode == 1
    assert "exactly one" in doubled.stderr
    assert _OLD in manifest.read_text(encoding="utf-8")


def test_pin_rejects_a_digest_that_is_not_64_hex_digits(tmp_path: Path) -> None:
    (tmp_path / "keystone-service.yaml").write_text(_SAMPLE, encoding="utf-8")
    completed = _run(tmp_path, "sha256:not-a-digest")
    assert completed.returncode == 2
    assert _OLD in (tmp_path / "keystone-service.yaml").read_text(encoding="utf-8")
