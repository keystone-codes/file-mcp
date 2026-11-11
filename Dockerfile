# ghcr.io/astral-sh/uv:python3.12-bookworm-slim
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

# python:3.12-slim-bookworm
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# The generated healthcheck calls wget or curl inside the container.
RUN groupadd --gid 10001 file-mcp \
    && useradd --uid 10001 --gid 10001 \
        --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin file-mcp \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# CVE-2026-84782: the base image ships openssl 3.0.22-1~deb12u1; the fix
# (3.0.22-1~deb12u2) is only in bookworm-security. libbrotli1 (pulled in by
# curl above) carries a fixed CRITICAL/HIGH finding on this base image too.
# CVE-2026-109518: apt 2.6.1 on this base image also carries a fixed HIGH
# finding. Run this after installing curl so libbrotli1 is already present
# to upgrade. Remove this layer once python:3.12-slim-bookworm is published
# with the patched packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade libssl3 openssl libbrotli1 apt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER 10001:10001

EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=2)"]

CMD ["python", "-m", "file_mcp"]
