"""Print the semantic models and metrics recorded in the semantic manifest."""

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[2] / "target" / "semantic_manifest.json"


def main() -> None:
    try:
        manifest = json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read semantic manifest: {MANIFEST}") from exc

    print("semantic models")
    for model in manifest["semantic_models"]:
        relation = (model.get("node_relation") or {}).get("relation_name", "")
        primary = model.get("primary_entity") or "-"
        print(f"  {model['name']:<22} primary={primary:<22} {relation}")

    print()
    print("metrics")
    for metric in manifest["metrics"]:
        params = metric["type_params"]
        agg = params.get("metric_aggregation_params") or {}
        if metric["type"] == "simple":
            detail = f"{agg.get('agg', '?')}({params.get('expr')}) on {agg.get('semantic_model')}"
        elif metric["type"] == "ratio":
            numerator = (params.get("numerator") or {}).get("name")
            denominator = (params.get("denominator") or {}).get("name")
            detail = f"{numerator} / {denominator}"
        else:
            detail = params.get("expr") or ""
        filter_spec = metric.get("filter") or {}
        filters = [
            f["where_sql_template"] for f in filter_spec.get("where_filters", [])
        ]
        suffix = f"   filter: {filters[0]}" if filters else ""
        print(f"  {metric['name']:<22} {metric['type']:<8} {detail}{suffix}")


if __name__ == "__main__":
    main()
