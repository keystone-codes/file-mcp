# file-mcp

file-mcp stores a file in an S3-compatible bucket and hands the caller a short-lived link. An agent uses the tools. A browser, or another service the agent is driving, uses the link. There is no API for other services to call file-mcp on a person's behalf.

How Keystone authenticates to an upstream is described in [Connecting services](https://github.com/keystone-codes/keystone-mcp/blob/release/0.5.0/docs/connecting-services.md).

## What it does

Two sides, and nothing else:

- **Tools** for the agent: `create_upload_url`, `get_download_link`, `get_file_info`. Every MCP request needs Keystone's bearer token. Every tool call also needs a signed `X-Keystone-Identity` assertion. Discovery (`tools/list`) needs only the bearer.
- **Public signed paths** `PUT /u/{token}` and `GET /d/{token}`. The token is the permission. These paths do not check the bearer.

File bytes never travel in a tool argument. A service that wants to process many objects of its own asks for an `s3` resource. It does not call file-mcp.

## Tools

Published names use the namespace `files` unless the installer renamed it: `files_create_upload_url`, `files_get_download_link`, `files_get_file_info`.

`create_upload_url(filename?, content_type?, link_ttl_seconds?, retention_days?)` returns:

```json
{
  "upload_url": "https://files.example.com/u/<token>",
  "file_id": "eph:uploads/2f1c0e3a-7b4d-4e6a-9c1d-8a0b5e2f4c31",
  "expires_at": "2026-10-13T12:15:00Z",
  "max_bytes": 104857600,
  "retention_until": "2026-11-12T12:00:00Z"
}
```

`PUT` the bytes to `upload_url` (for example `curl -T data.bin`). When the bucket cannot store a lifecycle rule the response also contains `"warnings": ["retention_unsupported"]`. `link_ttl_seconds` defaults to `FILE_MCP_LINK_TTL_SECONDS` and must be an integer from 1 to `FILE_MCP_LINK_TTL_MAX_SECONDS`. `retention_days` defaults to `FILE_MCP_UPLOAD_TTL_DAYS` and must be an integer from 1 to `FILE_MCP_UPLOAD_TTL_MAX_DAYS`.

`get_download_link(file_id, link_ttl_seconds?)` returns:

```json
{
  "file_url": "https://files.example.com/d/<token>",
  "expires_at": "2026-10-13T12:15:00Z"
}
```

Anyone who holds `file_url` can download the file until `expires_at`. Pass that link only to the person or service that needs the file.

`get_file_info(file_id)` returns:

```json
{
  "file_id": "eph:uploads/2f1c0e3a-7b4d-4e6a-9c1d-8a0b5e2f4c31",
  "filename": "data.bin",
  "content_type": "application/octet-stream",
  "size": 12582912,
  "created_at": "2026-10-13T12:00:00Z",
  "expires_at": "2026-11-12T12:00:00Z"
}
```

`filename` is JSON `null` when the upload did not name a file. `expires_at` is the time before which the storage will not delete the file. It follows the object's `retention-days` tag.

Tool errors are JSON with `code` and `message`:

| code | when |
|---|---|
| `identity_rejected` | the assertion is missing or does not verify |
| `invalid_argument` | filename, content type, or a lifetime is outside the rules above |
| `invalid_file_id` | the id was not issued by this service |
| `file_not_found` | the object is missing, not uploaded yet, or not owned by the caller. The message is always `file not found or not yours` |
| `storage_unavailable` | object storage failed. The message is `storage is temporarily unavailable`; the S3 code is only in the log |

`PUT /u/{token}` and `GET /d/{token}`:

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

## The `file_id` contract

`file_id` is an opaque string with the prefix `eph:`. Do not parse it, do not build one, and do not store it as an object key. file-mcp accepts only ids it issued. A foreign prefix or a bare object key is `invalid_file_id`.

A download link and file info are returned only to the subject (`sub`) that uploaded the file. Everyone else, including a caller with an administrator role, gets `file_not_found`. The response does not say whether the file exists.

The file is kept for `FILE_MCP_UPLOAD_TTL_DAYS` days (default 30) when the bucket accepts a lifecycle rule. If it does not, the service logs a warning, the upload still succeeds, and the operator configures expiry on the bucket.

Links last `FILE_MCP_LINK_TTL_SECONDS` seconds (default 900). Whoever holds a download link can fetch the bytes until it expires, so hand the link only to the party that needs the file.

## Passing a file to another service

Services do not call each other, and they do not receive each other's tokens. The agent calls `files_get_download_link`, receives `file_url`, and passes that URL as an argument to the second service's tool. To let the second service write a result back, the agent calls `files_create_upload_url` first and passes `upload_url` to that service. The same URL works from outside the network and from inside it.

A service that processes objects in bulk declares its own `needs: s3` resource. It does not read or write through file-mcp.

## Configuration

All variables use the `FILE_MCP_` prefix. Secrets are read only from the environment or from a file of mode `600`. Do not pass them on the command line.

The installer writes three groups, all mode `600`:

| File | What it holds |
|---|---|
| `packages/file-mcp.env` | S3 settings (`needs` does not limit containers, so `bind_env` is shared) and the generated `FILE_MCP_SIGNING_SECRET` |
| `packages/file-mcp.mcp.env` | bearer, identity secret, and registry name (the assertion audience) |

There is no `packages/file-mcp.s3.env`.

| Variable | Default | Who sets it | Rule |
|---|---|---|---|
| `FILE_MCP_HOST` | `0.0.0.0` | operator, via `--set` | Bind address. Not in the package manifest |
| `FILE_MCP_PORT` | `8081` | operator, via `--set` | 1–65535. Not in the package manifest |
| `FILE_MCP_PUBLIC_BASE_URL` | required | installer | `https://<service domain>`, no path. `http` is accepted for `localhost` and `127.0.0.1`; any other `http` host logs a warning |
| `FILE_MCP_REGISTRY_NAME` | `file-mcp` | installer | Registry entry name. This is the assertion `aud` |
| `FILE_MCP_BEARER_TOKEN` | required | installer | At least 32 bytes |
| `FILE_MCP_IDENTITY_SECRET` | required | installer | At least 32 bytes. Same value Keystone stores as `KEYSTONE_MCP_UPSTREAM_<NS>_IDENTITY_SECRET` |
| `FILE_MCP_IDENTITY_SECRET_PREVIOUS` | empty | operator, via `--set` | Previous identity secret during rotation. Not in the package manifest |
| `FILE_MCP_IDENTITY_ISSUER` | empty | operator, via `--set` | When set, the assertion `iss` must match. Not in the package manifest |
| `FILE_MCP_SIGNING_SECRET` | required | installer generates 48 bytes | Signs public links |
| `FILE_MCP_S3_ENDPOINT` | required | installer, from the `s3` resource | `http(s)://host[:port]` |
| `FILE_MCP_S3_BUCKET` | required | installer, from the `s3` resource | The service does not create the bucket |
| `FILE_MCP_S3_ACCESS_KEY` | required | installer, from the `s3` resource | 1–128 non-space characters |
| `FILE_MCP_S3_SECRET_KEY` | required | installer, from the `s3` resource | 1–128 non-space characters |
| `FILE_MCP_S3_REGION` | `us-east-1` | installer, from the `s3` resource | 1–32 characters, `a-z`, `0-9`, `-` |
| `FILE_MCP_S3_PATH_STYLE` | `true` | installer, from the `s3` resource | `true` or `false` |
| `FILE_MCP_S3_STARTUP_TIMEOUT_SECONDS` | `60` | operator, via `--set` | 0–600. Not in the package manifest |
| `FILE_MCP_LINK_TTL_SECONDS` | `900` | manifest default; override with `--set` | 60–86400 |
| `FILE_MCP_LINK_TTL_MAX_SECONDS` | `86400` | operator, via `--set` | 1–86400, and at least `FILE_MCP_LINK_TTL_SECONDS` |
| `FILE_MCP_UPLOAD_TTL_DAYS` | `30` | manifest default; override with `--set` | 1–3650 |
| `FILE_MCP_UPLOAD_TTL_MAX_DAYS` | `3650` | operator, via `--set` | 1–3650, and at least `FILE_MCP_UPLOAD_TTL_DAYS` |
| `FILE_MCP_MAX_BYTES` | `104857600` | manifest default; override with `--set` | 1–5368709120 |
| `FILE_MCP_LOG_LEVEL` | `INFO` | manifest default; override with `--set` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

A missing or invalid setting prints `config error: <NAME>: <reason>` (one line per problem, secret values omitted) and the process exits with code 2. If the bucket is missing, access is denied, or the endpoint stays unreachable, the process exits with code 3.

To run a checkout on one machine, build the image and pass an env file of mode `600`. The build prints an image id. Do not tag the image `latest`, and do not put a tag without a digest in `keystone-service.yaml`.

```bash
image="$(docker build -q .)"
docker run --rm --env-file ./file-mcp.env "$image"
```

`file-mcp.env` is not committed. `scripts/dev_call.py` calls one tool on a running service. It reads `.env` from the working directory. That file must be mode 600. Secrets are not accepted as arguments.

## Running behind Keystone

Install the package with an `s3/v1` resource, either an S3 endpoint you already run or MinIO from the catalog:

```bash
keystonectl service apply file-mcp
```

The installer writes the registry entry (`auth.bearer_env`, `auth.identity_secret_env`), the env files above, and a vhost that proxies only `/u/` and `/d/`. `/mcp` stays on the service network. Set nginx `client_max_body_size` to at least `FILE_MCP_MAX_BYTES` (100 MiB with the default).

`policy.example.yaml` is an example of a role that can call the three tools. The installer prints it and does not merge it into your policy.

An operator changes how the container is launched by editing `packages/file-mcp.override.yml`. That file survives `keystonectl service upgrade`. Taking the fragment over completely is `keystonectl service eject`. See [Operator layer](https://github.com/keystone-codes/keystonectl/blob/main/docs/spec/service-package.md#operator-layer).

After the service has a public domain, these two checks should look like this:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "https://<service-domain>/d/bad"
curl -s -o /dev/null -w '%{http_code}\n' "https://<service-domain>/mcp"
```

The first prints `404`. The second prints `404` from nginx, because `/mcp` is not proxied.

## Security notes

The log records the HTTP method, a redacted path, the status, and the duration. `PUT /u/<token>` and `GET /d/<token>` are logged as `/u/***` and `/d/***`. An upload also logs `file_id`, size, and the owner subject. An S3 failure logs the operation and the S3 error code. Bearer tokens, the identity secret, the signing secret, S3 keys, link tokens, and file bytes are not written to the log. Config errors name the variable and omit the value.

Uploads and downloads go through this service rather than an S3 presigned URL. The bucket credentials never leave the process, the link is an HMAC this service checks, and the size limit and the owner metadata are applied here. A presigned URL would hand the caller a credential for the bucket instead.

## Releasing

Tags are not moved and not deleted. A fix is a new version.

1. Set `version` in `pyproject.toml`, `src/file_mcp/__init__.py`, and `keystone-service.yaml` to the same `X.Y.Z`. The image line may still hold the previous digest; the workflow below replaces it.
2. Run the **Release image** workflow (`release-image.yml`) with that version. It checks that the tag `v<version>` does not exist, runs the tests and `keystonectl lint`, and pushes `ghcr.io/keystone-codes/file-mcp:<version>` for `linux/amd64` and `linux/arm64` with provenance and an SBOM. It does not push `latest`.
3. The workflow opens a pull request from `release/pin-<version>` whose only manifest change is the image digest (the multi-platform index). Review and merge it.
4. On that push to `main`, `tag-release.yml` reads the digest, checks it with `docker buildx imagetools inspect`, and only then tags `v<version>` and creates the GitHub Release. A mismatch fails the job and does not tag.
5. After the first publish, make the GHCR package `file-mcp` public in the organization package settings. A clean machine cannot pull a private package.

`keystonectl lint` in CI uses `keystonectl` from git commit `95052b21173a8af7aaa555eecaf46d9e378b438d` until the package is on PyPI.
