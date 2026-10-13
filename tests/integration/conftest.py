"""MinIO for the Docker checks.

Anonymous pulls of ``minio/minio`` were refused on 2026-10-13 (the repository
requires authorization). The fixture runs only when ``FILE_MCP_MINIO_IMAGE`` is
``minio/minio@sha256:<digest>`` of a release this machine can pull. A bare tag
is not accepted.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

_IMAGE_ENV = "FILE_MCP_MINIO_IMAGE"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_tcp(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"minio did not accept connections on 127.0.0.1:{port}")


@pytest.fixture(scope="session")
def minio(tmp_path_factory: pytest.TempPathFactory):
    image = os.environ.get(_IMAGE_ENV, "").strip()
    if not image.startswith("minio/minio@sha256:") or image.count(":") != 1:
        pytest.skip(
            "minio/minio is not anonymously pullable. "
            f"Set {_IMAGE_ENV}=minio/minio@sha256:<digest> to run these checks."
        )
    if (
        subprocess.call(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        != 0
    ):
        pulled = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if pulled.returncode != 0:
            pytest.skip(f"could not pull {image}")

    port = _free_port()
    name = f"ks-cat-file-mcp-f02-{os.getpid()}"
    secret_dir = tmp_path_factory.mktemp("minio-secret")
    env_file = Path(secret_dir) / "minio.env"
    access_key = "filemcp"
    secret_key = os.urandom(24).hex()
    env_file.write_text(
        f"MINIO_ROOT_USER={access_key}\nMINIO_ROOT_PASSWORD={secret_key}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:9000",
            "--env-file",
            str(env_file),
            image,
            "server",
            "/data",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.fail(started.stderr.strip() or "docker run failed")
    try:
        _wait_tcp(port, time.monotonic() + 30)
        endpoint = f"http://127.0.0.1:{port}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )
        deadline = time.monotonic() + 30
        bucket = "file-mcp-it"
        while True:
            try:
                client.create_bucket(Bucket=bucket)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        yield {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket": bucket,
            "client": client,
            "name": name,
        }
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, check=False)
        subprocess.run(["docker", "container", "rm", name], capture_output=True, check=False)
