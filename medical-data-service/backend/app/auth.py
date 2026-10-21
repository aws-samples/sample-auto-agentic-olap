"""Cognito authentication for the human-facing management surface.

Humans log in through the Cognito hosted UI (Authorization Code + PKCE) and the
SPA sends the resulting id_token as a Bearer token. This module verifies it.

Two dependencies:
  - require_user   any authenticated Cognito user (portal + data queries)
  - require_admin  only ADMIN_EMAIL (API key management)

The MCP endpoint does NOT use this; it is guarded by a Cognito OAuth access
token (app.mcp_auth) or, with MCP_OAUTH_ENABLED=false, by an API key.
"""

from __future__ import annotations

import os

import jwt
from fastapi import Depends, Header, HTTPException


class User:
    def __init__(self, sub: str, email: str) -> None:
        self.sub = sub
        self.email = email


_jwks_client: jwt.PyJWKClient | None = None


def _verify(token: str) -> User:
    global _jwks_client
    issuer = os.environ["COGNITO_ISSUER"]
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json")
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    client_id = os.getenv("COGNITO_CLIENT_ID")
    if not client_id:
        # Fail closed: without the portal app client id there is no way to tell
        # a portal token from one issued to any other client in the pool.
        raise jwt.InvalidAudienceError("COGNITO_CLIENT_ID is not configured")
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=client_id,
        options={"require": ["aud", "token_use", "exp"]},
    )
    if payload.get("token_use") != "id":
        raise jwt.InvalidTokenError("not an id token")
    return User(str(payload.get("sub", "")), str(payload.get("email", "")))


def require_user(authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return _verify(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc


def is_admin(user: User) -> bool:
    admin_email = os.getenv("ADMIN_EMAIL", "").lower()
    return bool(admin_email) and user.email.lower() == admin_email


def require_admin(user: User = Depends(require_user)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="admin only")
    return user
