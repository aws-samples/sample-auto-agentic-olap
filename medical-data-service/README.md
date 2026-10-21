# Medical Data Service - remote MCP data service

Expose the MetricFlow semantic layer of a pharma sales warehouse as a **remote
HTTP MCP server**. A local agent (Amazon Q / Claude Desktop, etc.) connects, and
a human asks questions and does analysis in natural language.

```text
local agent ──(MCP + Cognito OAuth, or API key)──▶ /mcp ──▶ MetricFlow semantic layer ──▶ Redshift
human (browser) ─(Cognito login)──▶ Vue front-end + /api/* ──▶ same domain
```

## Access control (two gates, no row-level data permissions)

There is no RLS/DDM - once past the gate, all metrics are visible. The two gates
only distinguish "human" from "machine":

| Path | Who | Auth |
| ------ | ------ | ------ |
| `/` `/api/config` `/health` | public | none |
| `/.well-known/oauth-protected-resource[/mcp]` | public (OAuth mode only) | none |
| `/api/info` `/api/metrics` `/api/query*` | human (browser) | Cognito login (JWT) |
| `/api/admin/keys` | admin | Cognito + email == `ADMIN_EMAIL` |
| `/mcp` | agent (machine) | **Cognito OAuth** (Bearer access token issued to the agent client) |

`/api/config` returns only what the portal needs before login (Cognito domain and
client ids, MCP auth mode). Whether the signed-in user is the admin comes from
`/api/info` (`is_admin`); the admin email is never sent to the browser.

- **The admin page uses Cognito**: a human opening the front-end signs in via the
  Cognito Hosted UI.
- **MCP uses Cognito OAuth**: connecting pops up Cognito sign-in (Authorization
  Code + PKCE), and every call carries the **signed-in user's own identity**
  (email) - see [docs/mcp-oauth.md](docs/mcp-oauth.md).
- You can also set `MCP_OAUTH_ENABLED=false` to switch back to **API Key** mode
  (`X-API-Key`, stored in Secrets Manager, managed by the admin on the front-end
  "API keys" page).

## Components

```text
backend/app/
  main.py            FastAPI: mounts MCP(/mcp) + REST(/api/*) + front-end static
  mcp/server.py      FastMCP: list_metrics / list_dimensions / preview_sql / run_query
  service.py         DataService: catalog + MetricFlow SQL generation + execution
  metricflow/        MetricFlow client (reads semantic_manifest.json, generates SQL)
  redshift/client.py Redshift Data API (executes SQL, returns columns + rows)
frontend/            Vue 3 + shadcn-vue portal (connection guide / metric catalog / query playground)
```

## MCP tools

| Tool | Purpose |
| ------ | ------ |
| `list_metrics` | List Pharma metrics, descriptions, and the dimensions available for each |
| `list_dimensions` | List Pharma groupable dimensions with descriptions (`entity__dimension` form) |
| `preview_sql` | Generate MetricFlow SQL only, without executing |
| `run_query` | Query Redshift using semantic-layer metrics; return the SQL and data rows |

## Environment variables

```text
COGNITO_ISSUER         https://cognito-idp.<region>.amazonaws.com/<UserPoolId>
COGNITO_CLIENT_ID      portal app client id (required: portal id tokens must carry it as aud; unset = all portal requests rejected)
COGNITO_DOMAIN         <prefix>.auth.<region>.amazoncognito.com
ADMIN_EMAIL            only this email can create/revoke API keys
MCP_API_KEYS_SECRET    Secrets Manager secret name that stores keys (default medical/dev/mcp-api-keys)
MCP_OAUTH_ENABLED      true = Cognito OAuth on /mcp, false = API key (code default false; Terraform default true)
MCP_RESOURCE_URL       public MCP URL, e.g. https://<domain>/mcp (OAuth mode)
MCP_AGENT_CLIENT_ID    public Cognito app client for agents; OAuth mode accepts only its tokens
REDSHIFT_WORKGROUP / REDSHIFT_DATABASE / REDSHIFT_SECRET_ARN / AWS_REGION
REDSHIFT_QUERY_TIMEOUT seconds before a statement is cancelled (default 300; keep <= ALB idle timeout)
QUERY_EXECUTION_ENABLED  whether to actually execute queries (default true)
METRICFLOW_MANIFEST_PATH semantic-manifest path (default /app/semantic_manifest.json in the image)
FRONTEND_DIST_DIR      built front-end directory to serve (set in the image)
```

## Running locally

Backend (needs the Redshift environment variables to actually execute queries):

```bash
cd backend
uv sync --frozen   # exact versions from uv.lock
export METRICFLOW_MANIFEST_PATH=../../medical-olap-dbt/target/semantic_manifest.json
export AWS_REGION=us-east-1
export REDSHIFT_WORKGROUP=medical-dev-wg REDSHIFT_DATABASE=medical_dw
export REDSHIFT_SECRET_ARN=<namespace admin secret arn>
export QUERY_EXECUTION_ENABLED=true
export COGNITO_ISSUER=... COGNITO_CLIENT_ID=... COGNITO_DOMAIN=... ADMIN_EMAIL=you@example.com

uv run --frozen uvicorn app.main:app --reload --port 8000
```

- MCP endpoint: `http://localhost:8000/mcp`
- REST: `GET /api/info`, `GET /api/metrics`, `POST /api/query/preview`, `POST /api/query`

You can also run MCP standalone, outside FastAPI: `uv run python -m app.mcp.server`
(Streamable HTTP). In API-key mode this has no key check, because `ApiKeyGuard`
lives in `main.py`: use it locally only.

Front-end:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, /api proxied to 8000
```

## Connecting MCP to a local agent

Remote MCP URL: `https://<your-domain>/mcp` (locally `http://localhost:8000/mcp`).

With the default OAuth mode, use the `mcp-remote` configuration in
[docs/mcp-oauth.md](docs/mcp-oauth.md#amazon-q--claude-integration). In API-key
mode (`MCP_OAUTH_ENABLED=false`), use this Amazon Q / Claude MCP config (write to
`~/.aws/amazonq/mcp.json` or the client config). The `X-API-Key` value is created
on the front-end "API keys" page:

```json
{
  "mcpServers": {
    "medical-data-service": {
      "url": "https://<your-domain>/mcp",
      "transport": "http",
      "headers": { "X-API-Key": "mk_live_..." }
    }
  }
}
```

Once connected, just ask in the conversation, e.g.:

> By region, show YTD 2026 sales value and YoY - which region has the lowest budget-achievement rate?

The agent first calls `list_metrics` / `list_dimensions` to find valid metric and
dimension names, then uses `run_query` to fetch data (`preview_sql` to see the SQL
only). All conventions are decided by the dbt semantic layer and MetricFlow; the
agent only picks metrics and grouping dimensions.

For how a single natural-language question is **broken down step by step into a
MetricFlow query, generates SQL, and executes/returns**, see the full call chain
and examples in [docs/query-flow.md](docs/query-flow.md).

## Deployment

`backend/Dockerfile` packages front-end + back-end + semantic-layer manifest
together. After changing the semantic layer, run `dbt parse` first, then
`backend/refresh-manifest.sh` to refresh the packaged manifest, then rebuild the
image.

The ECS task's environment variables include Cognito, `ADMIN_EMAIL`,
`MCP_API_KEYS_SECRET`, etc.; the task role needs Get/Put/Describe on that Secrets
Manager secret and, in OAuth mode, `cognito-idp:AdminGetUser` on the user pool.
