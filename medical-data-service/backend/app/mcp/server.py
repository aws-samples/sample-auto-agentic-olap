"""Remote MCP server for the medical sales data warehouse.

Exposed over Streamable HTTP so a local agent (Amazon Q, Claude Desktop, ...)
can connect to it as a remote MCP host and answer questions in natural
language. The application implements no authentication: the load balancer in
front of it validates the caller's token (see infra/terraform/alb.tf).

Tools (all go through the dbt / MetricFlow semantic layer):
  - list_metrics       what can be measured
  - list_dimensions    how it can be sliced
  - preview_sql        the SQL a query would run (no execution)
  - run_query          execute a metric query on Redshift and return rows
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.redshift.client import RedshiftTimeout
from app.service import data_service, execution_enabled

logger = logging.getLogger("mcp.query")

_common: dict[str, Any] = {
    "instructions": (
        "A MetricFlow semantic-layer query service for pharma sales data. First use list_metrics / list_dimensions "
        "to learn the available metrics and dimensions, then use run_query to fetch data and preview_sql to see the SQL only. "
        "Metrics such as pharma_sales_value (actual sales value), pharma_sales_ytd_yoy_rate (YTD YoY), "
        "pharma_budget_achievement_ytd (YTD comparable budget achievement rate); dimensions such as "
        "pharma_country__region_name (region), pharma_brand (brand), metric_time__month (month)."
    ),
    "stateless_http": True,
    "json_response": True,
    # Behind an ALB the Host header is the public domain, not localhost. The
    # MCP DNS-rebinding guard would reject it, so disable it here.
    "transport_security": TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
}

# No auth here on purpose: the Application Load Balancer validates the Cognito
# access token (jwt-validation listener rule) before a request can reach /mcp.
mcp = FastMCP("medical-data-service", **_common)
# Serve at the mount root so the public URL is a clean /mcp (see main.py).
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def list_metrics() -> list[dict[str, str]]:
    """List Pharma metrics. Each item has: name, description, and ``dimensions`` —
    a comma-separated string of the dimensions valid for that metric.

    IMPORTANT: valid dimensions are metric-specific. Only group a metric by a
    name that appears in that metric's own ``dimensions``. For example
    ``pharma_budget_value`` has no join path to ``pharma_package__bu_name``.
    Always use the exact names given (the ``entity__dimension`` form, e.g.
    ``pharma_country__region_name``), never a bare name like ``region_name``.
    """
    return [
        metric
        for metric in data_service().list_metrics()
        if metric["name"].startswith("pharma_")
    ]


@mcp.tool()
def list_dimensions() -> list[dict[str, str]]:
    """Pharma group-by dimensions in canonical form (``entity__dimension``).

    Whether a given metric can use a dimension is metric-specific — check the
    metric's own ``dimensions`` list from list_metrics before querying.
    """
    return [
        dimension
        for dimension in data_service().list_dimensions()
        if dimension["name"].startswith(("pharma_", "metric_time"))
    ]


@mcp.tool()
def preview_sql(
    metrics: list[str], group_by: list[str] | None = None, limit: int = 1000
) -> dict[str, Any]:
    """Render the SQL for a metric query without running it.

    Args:
        metrics: metric names, e.g. ["pharma_sales_value", "pharma_sales_quantity"].
        group_by: dimensions to slice by, e.g. ["pharma_country__region_name"].
        limit: max rows.
    """
    return data_service().generate_sql(metrics, group_by or [], limit)


@mcp.tool()
def run_query(
    metrics: list[str], group_by: list[str] | None = None, limit: int = 1000
) -> dict[str, Any]:
    """Run a metric query on Redshift and return the rows.

    Args:
        metrics: metric names, e.g. ["pharma_sales_value"].
        group_by: dimensions to slice by, e.g. ["metric_time__month", "pharma_brand"].
        limit: max rows (capped at 10000).

    Returns the generated SQL plus {columns, rows, row_count}.
    """
    logger.info("run_query metrics=%s group_by=%s", metrics, group_by)
    try:
        if not execution_enabled():
            return {
                "error": "query execution is disabled (set QUERY_EXECUTION_ENABLED=true)",
                **data_service().generate_sql(metrics, group_by or [], limit),
            }
        return {
            **data_service().run_query(metrics, group_by or [], limit),
        }
    except RedshiftTimeout as exc:
        # Most common failure: Redshift Serverless cold start / first-time
        # query compilation. Return a definitive result (with the SQL) so the
        # agent explains why and suggests a retry instead of looping.
        logger.info(
            "run_query timeout stmt=%s elapsed=%.0fs",
            exc.statement_id,
            exc.elapsed,
        )
        hint = (
            "The query timed out not because of data volume but due to Redshift Serverless cold start / first-query compilation"
            " (the same query usually returns in a few seconds once the plan is cached). Please wait a moment and retry; if it happens often,"
            " increase REDSHIFT_QUERY_TIMEOUT or keep the workgroup warm."
        )
        result: dict[str, Any] = {
            "error": str(exc),
            "reason": "redshift_cold_start_timeout",
            "retryable": True,
            "hint": hint,
            "metrics": metrics,
            "group_by": group_by or [],
        }
        with suppress(Exception):
            result.update(data_service().generate_sql(metrics, group_by or [], limit))
        return result
    except Exception as exc:  # noqa: BLE001
        # Return a clean, final error instead of raising, so the agent gets a
        # definitive "not possible" and does not loop retrying variations.
        msg = str(exc)
        hint = None
        if "join path" in msg or "group-by-item" in msg:
            hint = (
                "There is no join path between this metric and this dimension, so it cannot be grouped this way. This commonly happens when a metric that exists only on one fact table"
                " (e.g. pharma_budget_value, only on the budget/forecast table) is grouped by another dimension (e.g. pharma_package__bu_name)"
                ". Use a dimension both support, or switch to another metric."
            )
        logger.info("run_query failed error=%s", msg[:200])
        return {
            "error": msg[:500],
            "hint": hint,
            "retryable": False,
            "metrics": metrics,
            "group_by": group_by or [],
        }


def run() -> None:
    """Run standalone over Streamable HTTP (for local dev without FastAPI)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()
