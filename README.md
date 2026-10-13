# file-mcp

file-mcp is an MCP service that sits behind Keystone and stores files in an S3-compatible bucket. Callers reach it only through Keystone: every MCP request must carry Keystone's bearer token, and every tool call must carry a signed `X-Keystone-Identity` assertion. This release is the service skeleton (`/mcp`, `/healthz`, bearer and identity checks, S3 client). Tools and public download paths come later.

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
| `FILE_MCP_LINK_TTL_SECONDS` | `900` | 60–86400 |
| `FILE_MCP_UPLOAD_TTL_DAYS` | `30` | 1–3650 |
| `FILE_MCP_MAX_BYTES` | `104857600` | 1–5368709120 |
| `FILE_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

A missing or invalid setting prints `config error: <NAME>: <reason>` (one line per problem, secret values omitted) and the process exits with code 2. If the bucket is missing, access is denied, or the endpoint stays unreachable, the process exits with code 3.
