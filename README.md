# file-mcp

file-mcp is an MCP service that sits behind Keystone and stores files in an S3-compatible bucket. Callers reach the tools only through Keystone: every MCP request must carry Keystone's bearer token, and every tool call must carry a signed `X-Keystone-Identity` assertion. File bytes move on signed `PUT /u/{token}` and `GET /d/{token}` links and never through tool arguments.

How Keystone authenticates to an upstream is described in [Connecting services](https://github.com/keystone-codes/keystone-mcp/blob/release/0.5.0/docs/connecting-services.md).

## Run

```bash
docker build -t file-mcp:dev .
docker run --rm --env-file ./file-mcp.env file-mcp:dev
```

`file-mcp.env` is mode `600` and is not committed. Do not pass secrets on the command line.

## Settings

All variables use the `FILE_MCP_` prefix. Secrets are read only from the environment.

| Variable | Default | Rule |
|---|---|---|
| `FILE_MCP_HOST` | `0.0.0.0` | Bind address |
| `FILE_MCP_PORT` | `8081` | 1–65535 |
| `FILE_MCP_PUBLIC_BASE_URL` | required | `http` or `https`, no path, query, or fragment; a trailing `/` is removed. `http` with a host other than `localhost` or `127.0.0.1` logs a warning |
| `FILE_MCP_REGISTRY_NAME` | `file-mcp` | Keystone registry entry name; also the assertion `aud` |
| `FILE_MCP_BEARER_TOKEN` | required | At least 32 bytes. `openssl rand -base64 48` |
| `FILE_MCP_IDENTITY_SECRET` | required | At least 32 bytes. `openssl rand -base64 48` |
| `FILE_MCP_IDENTITY_SECRET_PREVIOUS` | empty | Previous identity secret during rotation; at least 32 bytes when set |
| `FILE_MCP_IDENTITY_ISSUER` | empty | When set, the assertion `iss` must match |
| `FILE_MCP_SIGNING_SECRET` | required | At least 32 bytes. `openssl rand -base64 48`. Signs public links |
| `FILE_MCP_S3_ENDPOINT` | required | `http(s)://host[:port]` |
| `FILE_MCP_S3_BUCKET` | required | Bucket name; the service does not create it |
| `FILE_MCP_S3_ACCESS_KEY` | required | 1–128 non-space characters |
| `FILE_MCP_S3_SECRET_KEY` | required | 1–128 non-space characters |
| `FILE_MCP_S3_REGION` | `us-east-1` | 1–32 characters, `a-z`, `0-9`, `-` |
| `FILE_MCP_S3_PATH_STYLE` | `true` | `true` or `false` |
| `FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS` | `60` | 0–600 |
| `FILE_MCP_LINK_TTL_SECONDS` | `900` | 60–86400. Default lifetime of a signed link |
| `FILE_MCP_LINK_TTL_MAX_SECONDS` | `86400` | 1–86400, and at least `FILE_MCP_LINK_TTL_SECONDS`. A tool may request a link lifetime from 1 up to this value |
| `FILE_MCP_UPLOAD_TTL_DAYS` | `30` | 1–3650. Default object retention |
| `FILE_MCP_UPLOAD_TTL_MAX_DAYS` | `3650` | 1–3650, and at least `FILE_MCP_UPLOAD_TTL_DAYS`. A tool may request retention from 1 up to this value |
| `FILE_MCP_MAX_BYTES` | `104857600` | 1–5368709120 |
| `FILE_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

A missing or invalid setting prints `config error: <NAME>: <reason>` (one line per problem, secret values omitted) and the process exits with code 2. If the bucket is missing, access is denied, or the endpoint stays unreachable, the process exits with code 3.

## Tools and links

The caller's subject is the owner of the file. `get_download_link` returns a link only to that owner. An administrator does not receive a link to someone else's file, and the error is the same as for a file that does not exist.

`create_upload_url(filename?, content_type?, link_ttl_seconds?, retention_days?)` returns `upload_url`, `file_id`, `expires_at`, `max_bytes`, and `retention_until`. `upload_url` is `{FILE_MCP_PUBLIC_BASE_URL}/u/{token}`. Put the bytes there (for example `curl -T`). Do not put file bytes in tool arguments. `link_ttl_seconds` defaults to `FILE_MCP_LINK_TTL_SECONDS` and must be an integer from 1 to `FILE_MCP_LINK_TTL_MAX_SECONDS`. `retention_days` defaults to `FILE_MCP_UPLOAD_TTL_DAYS` and must be an integer from 1 to `FILE_MCP_UPLOAD_TTL_MAX_DAYS`. `retention_until` is the time before which the storage will not delete the file. When the bucket cannot store a lifecycle rule, the response also contains `warnings: ["retention_unsupported"]` and the object is still accepted.

`get_download_link(file_id, link_ttl_seconds?)` returns `file_url` and `expires_at`. `file_url` is `{FILE_MCP_PUBLIC_BASE_URL}/d/{token}`. Anyone who has the link can download the file until `expires_at`.

`get_file_info(file_id)` returns `file_id`, `filename` (or null), `content_type`, `size`, `created_at`, and `expires_at`. `expires_at` is the time before which the storage will not delete the file. It follows the object's `retention-days` tag, which is `FILE_MCP_UPLOAD_TTL_DAYS` when the caller did not pass `retention_days`.

`file_id` is an opaque `eph:uploads/<uuid4>` string, with an optional lowercase extension. Bare object keys and other prefixes are rejected.

Tool errors are JSON with `code` and `message`:

| code | when |
|---|---|
| `identity_rejected` | the assertion is missing or does not verify |
| `invalid_argument` | filename, content type, or a lifetime is outside the rules above |
| `invalid_file_id` | the id was not issued by this service |
| `file_not_found` | the object is missing, not uploaded yet, or not owned by the caller. The message is always `file not found or not yours` |
| `storage_unavailable` | object storage failed. The message is `storage is temporarily unavailable`; the S3 code is only in the log |

`PUT /u/{token}` and `GET /d/{token}` do not use the bearer token. The signed token is the permission. Link tokens are not written to the log.

| `PUT /u/{token}` | body |
|---|---|
| 201 | `{"file_id", "size", "content_type"}` |
| 404 | `{"error": "not_found"}` for a bad, expired, or wrong-kind token |
| 409 | `{"error": "already_uploaded"}` |
| 413 | `{"error": "payload_too_large"}` |
| 415 | `{"error": "unsupported_content_type"}` |
| 422 | `{"error": "empty_body"}` |
| 405 | wrong method |
| 503 | `{"error": "storage_unavailable"}` |

| `GET /d/{token}` | response |
|---|---|
| 200 | the file bytes, with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: private, no-store` |
| 404 | `{"error": "file_not_found"}` |
| 405 | wrong method |
| 503 | `{"error": "storage_unavailable"}` |

Objects under `uploads/` are removed by a bucket lifecycle rule after `FILE_MCP_UPLOAD_TTL_DAYS`, unless a longer `retention_days` was requested. Each object is also tagged `retention-days`. If lifecycle configuration is rejected, the service still starts and still stores the file.

If `FILE_MCP_MAX_BYTES` is raised above 100 MiB, the nginx vhost in front of the service has to raise `client_max_body_size` as well.

`scripts/dev_call.py` calls one tool on a running service. It reads `.env` from the working directory. That file must be mode 600. Secrets are not accepted as arguments.
