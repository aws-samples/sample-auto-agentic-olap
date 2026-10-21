"""Compile metrics offline with MetricFlow and report their valid dimensions.

For each metric: list the dimensions MetricFlow can group it by, then render
SQL for (a) metric_time at the metric's grain and (b) metric_time plus one
entity dimension. Nothing is executed and no warehouse connection is used.

Usage:
    python check_metrics.py --manifest target/semantic_manifest.json \
        --metrics pharma_a,pharma_b [--dialect redshift] [--sql]

Needs a Python environment with metricflow installed (the version the serving
layer uses), e.g. the backend's .venv.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

DIALECTS = {
    "redshift": ("REDSHIFT", "metricflow.sql.render.redshift", "RedshiftSqlPlanRenderer"),
    "postgres": ("POSTGRES", "metricflow.sql.render.postgres", "PostgresSQLSqlPlanRenderer"),
    "snowflake": ("SNOWFLAKE", "metricflow.sql.render.snowflake", "SnowflakeSqlPlanRenderer"),
    "duckdb": ("DUCKDB", "metricflow.sql.render.duckdb_renderer", "DuckDbSqlPlanRenderer"),
    "bigquery": ("BIGQUERY", "metricflow.sql.render.big_query", "BigQuerySqlPlanRenderer"),
}


class RenderOnlyClient:
    def __init__(self, dialect: str) -> None:
        engine, module, cls = DIALECTS[dialect]
        self._engine = getattr(SqlEngine, engine)
        self._renderer = getattr(import_module(module), cls)()

    @property
    def sql_engine_type(self):  # type: ignore[no-untyped-def]
        return self._engine

    @property
    def sql_plan_renderer(self):  # type: ignore[no-untyped-def]
        return self._renderer


def render(engine: MetricFlowEngine, metrics: list[str], group_by: list[str]) -> str:
    request = MetricFlowQueryRequest.create(metric_names=metrics, group_by_names=group_by, limit=100)
    task = engine.explain(request).convert_to_execution_plan_result.execution_plan.tasks[0]
    return task.sql_statement.without_descriptions.sql


def group_name(dim) -> str:  # type: ignore[no-untyped-def]
    return "metric_time" if dim.name == "metric_time" else (getattr(dim, "dunder_name", None) or dim.name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--metrics", required=True, help="comma-separated metric names")
    parser.add_argument("--dialect", default="redshift", choices=sorted(DIALECTS))
    parser.add_argument("--sql", action="store_true", help="print the full SQL")
    args = parser.parse_args()
    # The dbt-generated manifest still needs MetricFlow transforms (e.g. COUNT -> SUM).
    manifest = parse_manifest_from_dbt_generated_manifest(Path(args.manifest).read_text())
    engine = MetricFlowEngine(SemanticManifestLookup(manifest), RenderOnlyClient(args.dialect))
    by_name = {m.name: m for m in manifest.metrics}

    report, failures = [], 0
    for name in [n.strip() for n in args.metrics.split(",") if n.strip()]:
        entry: dict = {"metric": name}
        if name not in by_name:
            entry["error"] = "not in manifest"
            failures += 1
            report.append(entry)
            continue
        try:
            dims = sorted({group_name(d) for d in engine.simple_dimensions_for_metrics([name])})
            grain = (by_name[name].time_granularity or "month")
            time = f"metric_time__{grain}"
            entity = next((d for d in dims if d != "metric_time" and "__" not in d), None) or next(
                (d for d in dims if d != "metric_time"), None)
            entry["dimensions"] = len(dims)
            entry["queries"] = []
            for group_by in ([time], [time, entity] if entity else None):
                if group_by is None:
                    continue
                sql = render(engine, [name], group_by)
                entry["queries"].append({"group_by": group_by, "sql": sql if args.sql else sql[:400]})
        except Exception as exc:  # noqa: BLE001 - report every metric
            entry["error"] = str(exc)[:800]
            failures += 1
        report.append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nchecked {len(report)} metrics, {failures} failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
