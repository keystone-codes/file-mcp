# Changelog

## [Unreleased]

### Added

- Tools create_upload_url, get_download_link, get_file_info; signed PUT /u/{token} and GET /d/{token} streamed through the service; owner of every file is the caller's subject; uploads/ expire after FILE_MCP_UPLOAD_TTL_DAYS.
- Service skeleton: MCP over Streamable HTTP on /mcp, /healthz, bearer and X-Keystone-Identity verification, S3 client.
