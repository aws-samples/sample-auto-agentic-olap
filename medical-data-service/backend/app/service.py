"""Core data service: turn metric requests into SQL and (optionally) run them.

No authentication, no row-level security, no column masking. This is a plain
analytics gateway: the semantic layer defines the metrics and dimensions, and
this module renders SQL through MetricFlow and executes it on Redshift.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.metricflow.client import MetricFlowClient


class DataService:
    def __init__(self) -> None:
        self.mf = MetricFlowClient()

    # -- catalog -------------------------------------------------------------

    @staticmethod
    def _group_by_name(dim) -> str:
        """The exact name MetricFlow accepts in group_by.

        Joined dimensions must be addressed as ``entity__dimension`` (that is
        the ``dunder_name``, e.g. ``pharma_package__brand_name``). Time is queried as
        ``metric_time`` regardless of its default grain.
        """
        if dim.name == "metric_time":
            return "metric_time"
        return getattr(dim, "dunder_name", None) or dim.name

    def _dims_for_metric(self, metric_name: str) -> list[str]:
        dims = self.mf.engine.simple_dimensions_for_metrics([metric_name])
        return sorted({self._group_by_name(d) for d in dims})

    def _all_dim_objects(self):
        """Every dimension object across all metrics (carries dunder_name +
        description), keyed by the canonical group-by name."""
        by_name: dict[str, Any] = {}
        for metric in self.mf.manifest.metrics:
            for d in self.mf.engine.simple_dimensions_for_metrics([metric.name]):
                by_name.setdefault(self._group_by_name(d), d)
        return by_name

    def list_metrics(self) -> list[dict[str, str]]:
        """Every metric, with its description and the dimensions valid for it.

        All values are strings (``dimensions`` is a comma-separated string, not a
        list) so the tool's output schema stays dict[str, str] — compatible with
        MCP clients that cached the earlier schema.

        The valid dimension list is metric-specific: e.g. ``pharma_budget_value`` has
        no join path to ``pharma_package__bu_name``, so it will not appear under
        that metric. Group by only names listed here for the metric.
        """
        descriptions = {m.name: (m.description or "") for m in self.mf.manifest.metrics}
        out = []
        for name in sorted(descriptions):
            out.append({
                "name": name,
                "description": descriptions[name],
                "dimensions": ", ".join(self._dims_for_metric(name)),
            })
        return out

    def list_dimensions(self) -> list[dict[str, str]]:
        """All group-by dimensions, using the canonical MetricFlow name.

        Names are the ``entity__dimension`` form (e.g. ``pharma_package__brand_name``),
        which is what group_by expects. Validity is per metric, so prefer the
        per-metric list from list_metrics; this is a global catalogue.
        """
        return sorted(
            (
                {"name": name, "description": getattr(d, "description", "") or ""}
                for name, d in self._all_dim_objects().items()
            ),
            key=lambda item: item["name"],
        )

    # -- query ---------------------------------------------------------------

    def generate_sql(
        self, metrics: list[str], group_by: list[str], limit: int = 1000
    ) -> dict[str, Any]:
        """Render the SQL for a metric query without executing it."""
        return self.mf.explain(metrics, group_by, min(limit, 10000))

    def run_query(
        self, metrics: list[str], group_by: list[str], limit: int = 1000
    ) -> dict[str, Any]:
        """Render the SQL and execute it on Redshift, returning rows.

        Execution requires REDSHIFT_SECRET_ARN (and the Data API permissions).
        The generated SQL is always returned alongside the data for
        transparency.
        """
        generated = self.generate_sql(metrics, group_by, limit)
        from app.redshift.client import RedshiftDataApi

        result = RedshiftDataApi().query_records(generated["sql"])
        return {**generated, **result}


@lru_cache(maxsize=1)
def data_service() -> DataService:
    """One shared instance so the semantic manifest is parsed only once."""
    return DataService()


def execution_enabled() -> bool:
    return os.getenv("QUERY_EXECUTION_ENABLED", "true").lower() == "true"
