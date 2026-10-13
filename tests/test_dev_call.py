"""The developer helper must not print secrets."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev_call.py"
_SENTINEL = "SENTINEL-BEARER-VALUE-0123456789abcd"


def test_dev_call_refuses_loose_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"FILE_MCP_BEARER_TOKEN={_SENTINEL}\n", encoding="utf-8")
    env_file.chmod(0o644)
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "get_file_info", "--file-id", "eph:uploads/x"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "mode 600" in completed.stderr
    assert _SENTINEL not in completed.stdout
    assert _SENTINEL not in completed.stderr


def test_dev_call_does_not_print_env_secrets(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"FILE_MCP_BEARER_TOKEN={_SENTINEL}\n", encoding="utf-8")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "create_upload_url", "--filename", "a.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert _SENTINEL not in completed.stdout
    assert _SENTINEL not in completed.stderr
    assert "FILE_MCP_PUBLIC_BASE_URL" in completed.stderr
