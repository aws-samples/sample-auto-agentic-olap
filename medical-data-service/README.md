# Medical Data Service - remote MCP data service

Expose the MetricFlow semantic layer of a pharma sales warehouse as a **remote
HTTP MCP server**. A local agent (Amazon Q / Claude Desktop, etc.) connects, and
a human asks questions and does analysis in natural language.

```text
local agent ──(MCP + Cognito OAuth)──▶ ALB (jwt-validation) ──▶ /mcp ──▶ MetricFlow semantic layer ──▶ Redshift
human (browser) ─(Cognito login)──▶ ALB (jwt-validation on /api) ──▶ Vue front-end + /api/* ──▶ same domain
```

## Access control (done by the ALB, no row-level data permissions)

The application implements no authentication or authorization. The ALB
validates Cognito tokens with its managed `jwt-validation` action before a
request reaches `/api/*` or `/mcp` (`infra/terraform/alb.tf`), and the service
security group accepts traffic from the ALB only. There is no RLS/DDM - once
past the ALB, all metrics are visible.

| Path | Who | Checked by the ALB |
| ------ | ------ | ------ |
| `/` `/assets/*` `/api/config` `/health` | public | nothing |
| `/.well-known/oauth-protected-resource[/mcp]` | public | nothing (OAuth discovery data) |
| `/api/info` `/api/metrics` `/api/query*` | human (browser) | Cognito ID token, `aud` = portal client |
| `/mcp` | agent (machine) | Cognito access token, `client_id` = agent client |

`/api/config` returns only what the portal needs before login (Cognito domain and
client ids).

- **The portal uses Cognito**: a human opening the front-end signs in via the
  Cognito Hosted UI (Authorization Code + PKCE) and the SPA sends the ID token.
- **MCP uses Cognito OAuth**: connecting pops up Cognito sign-in, and the agent
  sends the access token - see [docs/mcp-oauth.md](docs/mcp-oauth.md).

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
COGNITO_ISSUER         https://cognito-idp.<region>.amazonaws.com/<UserPoolId> (named in the OAuth metadata)
COGNITO_CLIENT_ID      portal app client id (sent to the SPA for hosted-UI login)
COGNITO_DOMAIN         <prefix>.auth.<region>.amazoncognito.com
MCP_RESOURCE_URL       public MCP URL, e.g. https://<domain>/mcp (named in the OAuth metadata)
MCP_AGENT_CLIENT_ID    public Cognito app client for agents (shown in the portal's connection guide)
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
export COGNITO_ISSUER=... COGNITO_CLIENT_ID=... COGNITO_DOMAIN=...

uv run --frozen uvicorn app.main:app --reload --port 8000
```

Run locally there is no ALB in front, so **the service has no authentication**.
uvicorn binds to `127.0.0.1` by default; keep it that way.

- MCP endpoint: `http://localhost:8000/mcp`
- REST: `GET /api/info`, `GET /api/metrics`, `POST /api/query/preview`, `POST /api/query`

You can also run MCP standalone, outside FastAPI: `uv run python -m app.mcp.server`
(Streamable HTTP, also without authentication: use it locally only).

Front-end:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, /api proxied to 8000
```

## Connecting MCP to a local agent

Remote MCP URL: `https://<your-domain>/mcp` (locally `http://localhost:8000/mcp`).

Use the `mcp-remote` configuration in
[docs/mcp-oauth.md](docs/mcp-oauth.md#amazon-q--claude-integration) (write to
`~/.aws/amazonq/mcp.json` or the client config).

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

The ECS task's environment variables carry the Cognito ids the portal and the
OAuth metadata need. The task role has no Cognito or API-key permissions: token
validation is done by the ALB.
