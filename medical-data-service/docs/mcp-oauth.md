# MCP OAuth integration

An agent connecting to the MCP endpoint (`https://your-app.example.com/mcp`) must
present a Cognito access token obtained through **Cognito sign-in**.

The token is validated by the **Application Load Balancer**, not by this
application. A `jwt-validation` listener rule on `/mcp` and `/mcp/*`
(`infra/terraform/alb.tf`) checks:

- the RS256 signature, against the user pool's JWKS
- `iss` (the user pool) and `exp`
- `token_use = access` (ID tokens are rejected)
- `client_id` = the agent app client (`cognito_agent_client_id`)

A request that fails any check is rejected by the ALB and never reaches the
service. The portal's `/api/*` is protected the same way, with ID tokens from
the portal app client. The application itself contains no token verification,
API keys or admin role, and the service security group accepts traffic from the
ALB only.

## Authentication flow (happens automatically)

```
agent connects to /mcp without a token
  → the ALB rejects it with 401
  → agent reads /.well-known/oauth-protected-resource/mcp (served by the app,
    left open by the ALB) and learns the authorization server = Cognito
  → agent reads Cognito's openid-configuration to get the authorize/token endpoints
  → pops up Cognito sign-in (Authorization Code + PKCE)
  → on success → gets an access token
  → agent reconnects to /mcp with the Bearer token → the ALB validates it and forwards
```

The protected-resource metadata is discovery data only: it names the issuer and
grants nothing.

## One prerequisite: Cognito does not support DCR

Cognito has no dynamic client registration (RFC 7591), so the agent **cannot
auto-obtain a client_id** and must use our **pre-created public client_id**:

```
client_id: <AGENT_APP_CLIENT_ID>   (public client, PKCE, no secret)
```

This client_id is not a password; it can be public and shared by all agents; the
real identity comes from each person's own Cognito sign-in. The ALB accepts only
tokens issued to this client. (It is also returned in `/api/config`.)

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
        "-y", "mcp-remote@0.15.2", "https://your-app.example.com/mcp/", "3334",
        "--static-oauth-client-info",
        "{\"client_id\":\"<AGENT_APP_CLIENT_ID>\",\"token_endpoint_auth_method\":\"none\"}"
      ]
    }
  }
}
```

The first connection opens a browser for you to sign in with Cognito; afterward
`mcp-remote` caches the token.

`mcp-remote` handles your OAuth tokens, so the config pins an exact version
(`mcp-remote@0.15.2`). Review the release before changing the version.

> Two key points:
>
> 1. Use `--static-oauth-client-info` (**not** `--static-oauth-client-metadata`):
>    the former uses an existing client_id directly and skips DCR; the latter still
>    attempts DCR, which Cognito does not support and errors with "does not support
>    dynamic client registration".
> 2. Fix the callback port to `3334` (the positional argument after the URL),
>    otherwise mcp-remote's random port will not match Cognito's allow-listed
>    `http://localhost:3334/oauth/callback`.

## Identity and audit

The service does not see or record who is calling: the ALB has already decided
whether the request may pass. There are no per-user permissions (no RLS/DDM);
every authenticated caller sees every metric. If you need a per-user audit
trail, enable ALB access logs or add one in your own deployment.

## AWS resources involved

```
Cognito user pool   <REGION>_<POOL_ID>
portal app client   <PORTAL_APP_CLIENT_ID> (portal sign-in; ID tokens on /api)
agent app client    <AGENT_APP_CLIENT_ID> (MCP OAuth, public client; access tokens on /mcp)
ALB listener rules  api_config (open), api (ID token), mcp (access token) in infra/terraform/alb.tf
```
