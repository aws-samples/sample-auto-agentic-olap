"""Auth hardening: MCP token verifier and the admin flag."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import mcp_auth
from app.auth import User, is_admin

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example"
AGENT_CLIENT = "agent-client-id"


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
    base = {"sub": "u1", "username": "u1", "token_use": "access", "client_id": AGENT_CLIENT, "exp": 9999999999}
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
        _claims(token_use="id"),
        {k: v for k, v in _claims().items() if k != "token_use"},
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
