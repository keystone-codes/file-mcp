# Changelog

## [1.0.2] - 2026-10-28

### Fixed

- Known CVEs in `fastmcp` (GHSA-7xgj-8392-5q4r, GHSA-vqqp-x5qm-5qp4) and in `authlib`, pulled in transitively through `fastmcp-slim` (CVE-2026-96760, CVE-2026-114615 through CVE-2026-114620): upgraded to `fastmcp` 4.1.4 and `authlib` 1.9.1.
- `libssl3`/`openssl` (CVE-2026-84782) and `libbrotli1` in the runtime image: the image now upgrades both after installing packages, instead of relying on whatever the base image shipped.

### Changed

- CI scans the release image with Trivy (`CRITICAL,HIGH`, `ignore-unfixed`) before it is pushed.
- Integration tests run against `ghcr.io/keystone-codes/minio-server`, which is anonymously pullable, instead of `minio/minio`, which stopped being anonymously pullable on 2026-10-13 and left the "Integration" CI job always skipping every test.

## [1.0.1] - 2026-10-15

### Fixed

- The manifest no longer calls the published image digest a placeholder.

### Changed

- CI lints the manifest with `keystonectl` 0.1.0 from PyPI.

## [1.0.0] - 2026-10-13

### Added

- Catalog package: `keystone-service.yaml` in the v1 `containers` form, `policy.example.yaml`, and release workflows that pin `ghcr.io/keystone-codes/file-mcp` by digest before the version tag is created.
- Tools `create_upload_url`, `get_download_link`, and `get_file_info`. Signed `PUT /u/{token}` and `GET /d/{token}` stream bytes through the service. The owner of a file is the caller's subject. Objects under `uploads/` expire after `FILE_MCP_UPLOAD_TTL_DAYS`.
- Service skeleton: MCP over Streamable HTTP on `/mcp`, `/healthz`, bearer and `X-Keystone-Identity` verification, and an S3 client.
