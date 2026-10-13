# Changelog

## [1.0.0] - 2026-10-13

### Added

- Catalog package: `keystone-service.yaml` in the v1 `containers` form, `policy.example.yaml`, and release workflows that pin `ghcr.io/keystone-codes/file-mcp` by digest before the version tag is created.
- Tools `create_upload_url`, `get_download_link`, and `get_file_info`. Signed `PUT /u/{token}` and `GET /d/{token}` stream bytes through the service. The owner of a file is the caller's subject. Objects under `uploads/` expire after `FILE_MCP_UPLOAD_TTL_DAYS`.
- Service skeleton: MCP over Streamable HTTP on `/mcp`, `/healthz`, bearer and `X-Keystone-Identity` verification, and an S3 client.
