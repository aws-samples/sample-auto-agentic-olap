"""Authentication lives in the load balancer, not in this application.

These tests keep it that way: the application must not verify tokens or keep
its own credentials, and the Terraform must keep the ALB rules that do.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.main import app

BACKEND = Path(__file__).resolve().parents[1]
ALB_TF = BACKEND.parents[1] / "infra" / "terraform" / "alb.tf"


def test_no_route_has_an_auth_dependency() -> None:
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            assert not dependant.dependencies, f"{route.path} has a dependency"


def test_application_does_not_verify_tokens() -> None:
    sources = "\n".join(p.read_text() for p in (BACKEND / "app").rglob("*.py"))
    for marker in ("import jwt", "from jwt", "TokenVerifier", "AuthSettings", "x-api-key"):
        assert marker not in sources, marker


def _rules() -> dict[str, str]:
    """Map each aws_lb_listener_rule name in alb.tf to its body."""
    text = ALB_TF.read_text()
    starts = [
        (m.group(1), m.end())
        for m in re.finditer(r'resource "aws_lb_listener_rule" "(\w+)" \{', text)
    ]
    bodies = {}
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(text)
        bodies[name] = text[start:end]
    return bodies


def test_alb_validates_tokens_for_mcp_and_api() -> None:
    rules = _rules()
    mcp = rules["mcp"]
    api = rules["api"]
    assert '"/mcp"' in mcp and '"/mcp/*"' in mcp
    assert '"/api/*"' in api
    for body in (mcp, api):
        assert '"jwt-validation"' in body
        assert "jwks_endpoint" in body and "issuer" in body
    assert "var.cognito_agent_client_id" in mcp and '"access"' in mcp
    assert "var.cognito_client_id" in api and '"id"' in api
