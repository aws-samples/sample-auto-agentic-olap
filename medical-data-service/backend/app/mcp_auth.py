"""OAuth Resource Server verification for the MCP endpoint.

The MCP server is an OAuth 2.1 Resource Server: the agent obtains an access
token from Cognito (Authorization Code + PKCE) and presents it as a Bearer
token. This verifier validates that token (RS256 via Cognito JWKS, issuer,
`token_use=access`, and `client_id` equal to MCP_AGENT_CLIENT_ID) and returns
the caller identity. Scopes are not checked.

Cognito access tokens carry `sub`, `username`, `client_id`, `scope`,
`token_use=access` — but NOT `email`. We surface `email` if a pre-token
customization adds it, otherwise fall back to `username`/`sub`.
"""

from __future__ import annotations

import os

import boto3
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

_jwks_client: jwt.PyJWKClient | None = None
_email_cache: dict[str, str | None] = {}


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        issuer = os.environ["COGNITO_ISSUER"]
        _jwks_client = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json")
    return _jwks_client


def _pool_id() -> str:
    # issuer = https://cognito-idp.<region>.amazonaws.com/<pool-id>
    return os.environ["COGNITO_ISSUER"].rstrip("/").rsplit("/", 1)[-1]


def _resolve_email(sub: str, username: str) -> str | None:
    """Cognito access tokens carry no email, so look it up by the user id.

    Cached per sub to avoid a Cognito call on every MCP request.
    """
    if sub in _email_cache:
        return _email_cache[sub]
    email = None
    try:
        client = boto3.client("cognito-idp", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = client.admin_get_user(UserPoolId=_pool_id(), Username=username or sub)
        attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
        email = attrs.get("email")
    except Exception:
        email = None
    _email_cache[sub] = email
    return email


class CognitoTokenVerifier(TokenVerifier):
    """Validate a Cognito access token and map it to an MCP AccessToken."""

    async def verify_token(self, token: str) -> AccessToken | None:
        issuer = os.environ["COGNITO_ISSUER"]
        try:
            signing_key = _jwks().get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=issuer,
                options={"verify_aud": False},  # Cognito access tokens have no `aud`
            )
        except jwt.PyJWTError:
            return None

        # Only accept access tokens (not id tokens, not tokens without the claim).
        if payload.get("token_use") != "access":
            return None

        # Only accept tokens issued to the agent app client. Cognito access
        # tokens have no `aud`, so `client_id` is what binds a token to this
        # resource. Fail closed if the allowed client is not configured.
        allowed_client = os.getenv("MCP_AGENT_CLIENT_ID", "").strip()
        if not allowed_client or payload.get("client_id") != allowed_client:
            return None

        scopes = payload.get("scope", "").split() if payload.get("scope") else []
        sub = str(payload.get("sub", ""))
        username = str(payload.get("username", ""))
        # Prefer an email claim; otherwise resolve it from Cognito by user id.
        identity = payload.get("email") or _resolve_email(sub, username) or username or sub
        return AccessToken(
            token=token,
            client_id=str(payload.get("client_id", "")),
            scopes=scopes,
            expires_at=int(payload["exp"]) if "exp" in payload else None,
            subject=str(payload.get("sub", "")),
            claims={**payload, "identity": identity},
        )


def caller_from_access_token(access) -> str:
    """Extract a human identity string from the MCP AccessToken."""
    if access is None:
        return "anonymous"
    claims = getattr(access, "claims", None) or {}
    return claims.get("identity") or getattr(access, "subject", None) or "unknown"
