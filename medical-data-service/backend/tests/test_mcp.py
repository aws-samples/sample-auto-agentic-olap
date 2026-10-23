"""Smoke tests for the MCP tools and the REST surface (no Redshift needed)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import data_service


def test_catalog_lists_metrics_and_dimensions() -> None:
    ds = data_service()
    metrics = ds.list_metrics()
    dims = ds.list_dimensions()
    assert any(m["name"] == "pharma_sales_value" for m in metrics)
    assert any(d["name"] == "pharma_country__region_name" for d in dims)


def test_generate_sql_hits_the_atomic_fact() -> None:
    result = data_service().generate_sql(
        ["pharma_sales_value"], ["pharma_country__region_name"]
    )
    assert "pharma_sales_detail" in result["sql"]


# No lifespan context manager: none of these requests reach the MCP app, so
# the one-shot MCP session manager must not be started (running it twice
# across tests raises).
_client = TestClient(app)


def test_config_is_served() -> None:
    # /api/config is what the SPA reads before login; the ALB leaves it open.
    assert _client.get("/api/config").status_code == 200


def test_api_key_management_is_gone() -> None:
    for method in ("get", "post"):
        assert getattr(_client, method)("/api/admin/keys").status_code in (404, 405)


def test_openapi_docs_are_disabled() -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _client.get(path).status_code == 404


def test_protected_resource_metadata_points_at_cognito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example"
    monkeypatch.setenv("COGNITO_ISSUER", issuer)
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://example.com/mcp")
    body = _client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert body["authorization_servers"] == [issuer]
    assert body["resource"] == "https://example.com/mcp"


def test_public_catalogue_is_pharma_only() -> None:
    from app.mcp.server import list_dimensions, list_metrics

    metrics = list_metrics()
    dimensions = list_dimensions()

    assert metrics
    assert dimensions
    assert all(item["name"].startswith("pharma_") for item in metrics)
    assert any(item["name"].startswith("metric_time") for item in dimensions)
    assert all(
        item["name"].startswith(("pharma_", "metric_time")) for item in dimensions
    )


def test_preview_sql_uses_semantic_layer() -> None:
    from app.mcp.server import preview_sql

    result = preview_sql(
        ["pharma_sales_ytd_yoy_rate"], ["metric_time__month", "pharma_brand"]
    )
    assert "pharma_sales_detail" in result["sql"]
    assert "metricflow_time_spine" in result["sql"]


def test_run_query_returns_sql_when_execution_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp import server

    monkeypatch.setattr(server, "execution_enabled", lambda: False)

    result = server.run_query(["pharma_sales_value"], ["pharma_brand"])

    assert "disabled" in result["error"]
    assert "pharma_sales_detail" in result["sql"]


def test_run_query_explains_missing_join_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp import server

    monkeypatch.setattr(server, "execution_enabled", lambda: True)

    result = server.run_query(["pharma_budget_value"], ["pharma_package__bu_name"])

    assert result["retryable"] is False
    assert result["hint"]


def test_mcp_tools_are_registered() -> None:
    import asyncio

    from app.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {
        "list_metrics",
        "list_dimensions",
        "preview_sql",
        "run_query",
    }
