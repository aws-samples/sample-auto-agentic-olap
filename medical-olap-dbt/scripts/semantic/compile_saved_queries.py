"""Compile every saved query in the semantic manifest to Redshift SQL.

Renders SQL only; nothing is executed and no database connection is needed.
Run `scripts/dbt_parse.py` first so target/semantic_manifest.json exists.

Usage:
    python scripts/semantic/compile_saved_queries.py            # summary
    python scripts/semantic/compile_saved_queries.py --sql      # also print SQL
"""

import sys
from importlib import import_module
from pathlib import Path

DBT_ROOT = Path(__file__).resolve().parents[2]
REPO = DBT_ROOT.parent
sys.path.insert(0, str(REPO / "medical-data-service" / "backend"))

MetricFlowClient = import_module("app.metricflow.client").MetricFlowClient
MANIFEST = DBT_ROOT / "target" / "semantic_manifest.json"


def main() -> int:
    show_sql = "--sql" in sys.argv[1:]
    client = MetricFlowClient(str(MANIFEST))
    failures = 0
    for saved_query in client.manifest.saved_queries:
        try:
            explained = client.explain_saved_query(saved_query.name)
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"FAIL  {saved_query.name}: {exc}")
            continue
        print(f"ok    {saved_query.name}  metrics={len(explained['metrics'])}  "
              f"group_by={explained['group_by']}")
        if show_sql:
            print(explained["sql"], end="\n\n")
    total = len(client.manifest.saved_queries)
    print(f"\n{total - failures}/{total} saved queries compiled")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
