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


# No lifespan context manager: these requests short-circuit at the middleware
# or the auth dependency, so the one-shot MCP session manager must not be
# started (running it twice across tests raises).
_client = TestClient(app)


def test_config_is_public_but_info_requires_auth() -> None:
    # /api/config is public (SPA needs it before login)
    assert _client.get("/api/config").status_code == 200
    # /api/info now requires a Cognito token
    assert _client.get("/api/info").status_code == 401


def test_mcp_requires_api_key() -> None:
    # No API key -> rejected before reaching the MCP app
    assert _client.get("/mcp").status_code == 401
    assert _client.post("/mcp", headers={"X-API-Key": "bogus"}).status_code == 401


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

    monkeypatch.setattr(server, "OAUTH_ENABLED", False)
    monkeypatch.setattr(server, "execution_enabled", lambda: False)

    result = server.run_query(["pharma_sales_value"], ["pharma_brand"])

    assert "disabled" in result["error"]
    assert "pharma_sales_detail" in result["sql"]


def test_run_query_explains_missing_join_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp import server

    monkeypatch.setattr(server, "OAUTH_ENABLED", False)
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
