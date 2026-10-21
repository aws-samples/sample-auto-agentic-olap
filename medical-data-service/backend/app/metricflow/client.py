from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.redshift import RedshiftSqlPlanRenderer
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup


class ExplainOnlySqlClient:
    """Renderer-only client used by MetricFlow to produce SQL.

    Execution is deliberately performed by the Data Service (Redshift Data API),
    not by MetricFlow's internal executor.
    """

    @property
    def sql_engine_type(self) -> SqlEngine:
        return SqlEngine.REDSHIFT

    @property
    def sql_plan_renderer(self) -> RedshiftSqlPlanRenderer:
        return RedshiftSqlPlanRenderer()



class MetricFlowClient:
    def __init__(self, manifest_path: str | None = None) -> None:
        path = Path(manifest_path or os.getenv(
            "METRICFLOW_MANIFEST_PATH",
            "../../medical-olap-dbt/target/semantic_manifest.json",
        ))
        if not path.exists():
            raise FileNotFoundError(f"MetricFlow semantic manifest not found: {path}")
        # dbt writes the manifest before MetricFlow's own transforms (e.g. COUNT -> SUM);
        # this parser applies them, plain parse_raw does not.
        self.manifest = parse_manifest_from_dbt_generated_manifest(path.read_text())
        self.engine = MetricFlowEngine(SemanticManifestLookup(self.manifest), ExplainOnlySqlClient())

    def _render(self, request: MetricFlowQueryRequest) -> str:
        result = self.engine.explain(request)
        task = result.convert_to_execution_plan_result.execution_plan.tasks[0]
        if not task.sql_statement:
            raise RuntimeError("MetricFlow did not produce a SQL statement")
        return task.sql_statement.without_descriptions.sql

    def explain(self, metric_names: list[str], group_by_names: list[str], limit: int = 1000) -> dict[str, Any]:
        request = MetricFlowQueryRequest.create(
            metric_names=metric_names,
            group_by_names=group_by_names,
            limit=limit,
        )
        return {
            "metrics": metric_names,
            "group_by": group_by_names,
            "sql": self._render(request),
        }

    def saved_query(self, name: str) -> Any:
        for saved_query in self.manifest.saved_queries:
            if saved_query.name == name:
                return saved_query
        known = ", ".join(sorted(q.name for q in self.manifest.saved_queries))
        raise ValueError(f"saved query not found: {name}. Known: {known or '(none)'}")

    def explain_saved_query(self, name: str) -> dict[str, Any]:
        """Render a saved query, so a materialized cache and an ad-hoc query
        cannot disagree about a metric definition.

        No limit is applied: the output is meant to be written to a table.
        """
        saved_query = self.saved_query(name)
        request = MetricFlowQueryRequest.create(saved_query_name=name)
        return {
            "saved_query": name,
            "metrics": list(saved_query.query_params.metrics),
            "group_by": [str(item) for item in saved_query.query_params.group_by],
            "sql": self._render(request),
        }
