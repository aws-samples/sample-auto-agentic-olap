"""Auth hardening: MCP token verifier and the admin flag."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import mcp_auth
from app.auth import User, is_admin

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example"
AGENT_CLIENT = "agent-client-id"
# Cognito's token-type claim and its two values (not credentials).
USE_CLAIM = "token_use"
ACCESS = "access"
ID = "id"


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch):
    """Bypass signature checks so only the claim checks are exercised."""
    monkeypatch.setenv("COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_AGENT_CLIENT_ID", AGENT_CLIENT)
    monkeypatch.setattr(
        mcp_auth,
        "_jwks",
        lambda: SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key="k")),
    )
    monkeypatch.setattr(mcp_auth, "_resolve_email", lambda sub, username: None)

    def run(claims: dict):
        monkeypatch.setattr(mcp_auth.jwt, "decode", lambda *a, **k: claims)
        return asyncio.run(mcp_auth.CognitoTokenVerifier().verify_token("t"))

    return run


def _claims(**overrides) -> dict:
    base = {"sub": "u1", "username": "u1", USE_CLAIM: ACCESS, "client_id": AGENT_CLIENT, "exp": 9999999999}
    base.update(overrides)
    return base


def test_agent_access_token_is_accepted(verifier) -> None:
    token = verifier(_claims())
    assert token is not None
    assert token.client_id == AGENT_CLIENT


@pytest.mark.parametrize(
    "claims",
    [
        _claims(client_id="some-other-app-client"),
        {**_claims(), USE_CLAIM: ID},
        {k: v for k, v in _claims().items() if k != USE_CLAIM},
        {k: v for k, v in _claims().items() if k != "client_id"},
    ],
    ids=["other-client", "id-token", "missing-token-use", "missing-client-id"],
)
def test_other_tokens_are_rejected(verifier, claims: dict) -> None:
    assert verifier(claims) is None


def test_rejects_everything_when_agent_client_unset(verifier, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AGENT_CLIENT_ID")
    assert verifier(_claims()) is None


def test_is_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", "Admin@Example.com")
    assert is_admin(User("s", "admin@example.com"))
    assert not is_admin(User("s", "someone@example.com"))
    monkeypatch.delenv("ADMIN_EMAIL")
    assert not is_admin(User("s", "admin@example.com"))


def test_public_config_does_not_expose_admin_email(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    body = TestClient(app).get("/api/config").json()
    assert "admin_email" not in body
    assert "admin@example.com" not in str(body)


PORTAL_CLIENT = "portal-client-id"


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch):
    """Run require_user with signature checks bypassed but PyJWT's claim checks intact."""
    from app import auth

    monkeypatch.setenv("COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("COGNITO_CLIENT_ID", PORTAL_CLIENT)
    monkeypatch.setattr(
        auth,
        "_jwks_client",
        SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key="secret-for-tests-only-32-bytes!!")),
    )
    real_decode = auth.jwt.decode

    def fake_decode(token, key, **kwargs):
        kwargs["algorithms"] = ["HS256"]
        return real_decode(token, key, **kwargs)

    monkeypatch.setattr(auth.jwt, "decode", fake_decode)

    def run(claims: dict):
        token = auth.jwt.encode(claims, "secret-for-tests-only-32-bytes!!", algorithm="HS256")
        return auth.require_user(f"Bearer {token}")

    return run


def _portal_claims(**overrides) -> dict:
    base = {"sub": "u1", "email": "u1@example.com", "iss": ISSUER, "aud": PORTAL_CLIENT, USE_CLAIM: ID, "exp": 9999999999}
    base.update(overrides)
    return base


def test_portal_id_token_is_accepted(portal) -> None:
    assert portal(_portal_claims()).email == "u1@example.com"


@pytest.mark.parametrize(
    "claims",
    [
        _portal_claims(aud="some-other-app-client"),
        {**_portal_claims(), USE_CLAIM: ACCESS},
        {k: v for k, v in _portal_claims().items() if k != USE_CLAIM},
        {k: v for k, v in _portal_claims().items() if k != "aud"},
    ],
    ids=["other-client", "access-token", "missing-token-use", "missing-aud"],
)
def test_portal_rejects_other_tokens(portal, claims: dict) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        portal(claims)
    assert exc.value.status_code == 401


def test_portal_rejects_everything_when_client_id_unset(portal, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.delenv("COGNITO_CLIENT_ID")
    with pytest.raises(HTTPException) as exc:
        portal(_portal_claims())
    assert exc.value.status_code == 401
