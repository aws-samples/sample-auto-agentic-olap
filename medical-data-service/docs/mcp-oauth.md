# MCP OAuth integration

The MCP endpoint (`https://your-app.example.com/mcp`) is an **OAuth 2.1 Resource
Server**: an agent connecting must obtain an access token via **Cognito sign-in**.
The server checks the token's RS256 signature against the pool's JWKS, its issuer,
that it is an access token (`token_use=access`), and that it was issued to the
agent app client (`client_id` == `MCP_AGENT_CLIENT_ID`; if that variable is unset,
every token is rejected). It does not check audience or scopes. It then carries
the **signed-in user's own identity** (email) into every call (logs + the `caller`
field returned by `run_query`).

## Authentication flow (happens automatically)

```
agent connects to /mcp
  → 401 + WWW-Authenticate (points to /.well-known/oauth-protected-resource/mcp)
  → agent reads the metadata and learns the authorization server = Cognito
  → agent reads Cognito's openid-configuration to get the authorize/token endpoints
  → pops up Cognito sign-in (Authorization Code + PKCE)
  → on success → gets an access token
  → agent reconnects to /mcp with the Bearer token → passes, identity = the signed-in person
```

## One prerequisite: Cognito does not support DCR

Cognito has no dynamic client registration (RFC 7591), so the agent **cannot
auto-obtain a client_id** and must use our **pre-created public client_id**:

```
client_id: <AGENT_APP_CLIENT_ID>   (public client, PKCE, no secret)
```

This client_id is not a password; it can be public and shared by all agents; the
real identity comes from each person's own Cognito sign-in. The server accepts
only tokens issued to this client. (It is also returned in `/api/config`.)

## Amazon Q / Claude integration

Use `mcp-remote` as a bridge (it completes the OAuth handshake for the agent and
supports manually specifying a client_id to work around Cognito's lack of DCR).
Write to `~/.aws/amazonq/mcp.json` or the client's MCP config:

```json
{
  "mcpServers": {
    "medical-data-service": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://your-app.example.com/mcp/", "3334",
        "--static-oauth-client-info",
        "{\"client_id\":\"<AGENT_APP_CLIENT_ID>\",\"token_endpoint_auth_method\":\"none\"}"
      ]
    }
  }
}
```

The first connection opens a browser for you to sign in with Cognito; afterward
`mcp-remote` caches the token.

> Two key points:
>
> 1. Use `--static-oauth-client-info` (**not** `--static-oauth-client-metadata`):
>    the former uses an existing client_id directly and skips DCR; the latter still
>    attempts DCR, which Cognito does not support and errors with "does not support
>    dynamic client registration".
> 2. Fix the callback port to `3334` (the positional argument after the URL),
>    otherwise mcp-remote's random port will not match Cognito's allow-listed
>    `http://localhost:3334/oauth/callback`.

## How identity is surfaced

- **The access token has no email** (a Cognito trait); the server reverse-looks-up
  the email by user id via `admin-get-user` (with caching).
- Every `run_query` return carries `"caller": "<your email>"`, and the server log
  also records `run_query by=<email> ...`.
- Note: identity is currently used for **audit/traceability** and does not change
  the returned data (no per-user permissions/RLS).

## Two auth modes (switchable)

The server controls this with the `MCP_OAUTH_ENABLED` environment variable:

| Mode | MCP auth | Identity | Suitable for |
|------|---------|------|------|
| `MCP_OAUTH_ENABLED=true` (Terraform default; the code default when unset is `false`) | Cognito OAuth | the signed-in person (real-time) | needing a real human identity |
| `MCP_OAUTH_ENABLED=false` | API Key (`X-API-Key`) | the key creator (fixed at creation) | simple machine integration |

Switching needs no code change: set `mcp_oauth_enabled` in tfvars and re-apply.
Avoid hand-editing the task definition, because Terraform will drift from it and
`data-service.yml` copies the current task definition forward on every deploy.

## AWS resources involved

```
Cognito user pool   <REGION>_<POOL_ID> (medical-dev-users)
portal app client   <PORTAL_APP_CLIENT_ID> (admin-page sign-in)
agent app client    <AGENT_APP_CLIENT_ID> (MCP OAuth, public client)
task role permission cognito-idp:AdminGetUser (reverse-look-up email)
```
