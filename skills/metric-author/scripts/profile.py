"""Profile every semantic model of a dbt + MetricFlow project.

Structure (entities, dimensions, time grain, existing metrics) comes from the
semantic manifest; the full column list and tests come from the dbt model YAML;
column statistics come from running each model's SQL locally in DuckDB against
the project's seed CSVs (Redshift SQL is transpiled with sqlglot).

Usage:
    python profile.py --project <dbt_dir> [--manifest <path>] [--out <file>]

Run with: uv run --with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6 python profile.py ...
"""

from __future__ import annotations

import argparse
import json

import sys
from pathlib import Path

import duckdb
import jinja2
import sqlglot
import yaml

LOW_CARDINALITY = 20


def find_manifest(project: Path, explicit: str | None, config: dict) -> Path:
    candidates = [explicit] if explicit else config.get("manifest", ["target/semantic_manifest.json"])
    for item in candidates:
        path = (project / item).resolve()
        if path.is_file():
            return path
    raise SystemExit(f"semantic manifest not found (tried {candidates}); run dbt parse first")


def model_yaml(project: Path) -> dict[str, dict]:
    """Column lists and tests for every dbt model, keyed by model name."""
    models: dict[str, dict] = {}
    for path in (project / "models").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text()) or {}
        for model in doc.get("models", []) or []:
            columns = {}
            for column in model.get("columns", []) or []:
                tests = column.get("tests") or column.get("data_tests") or []
                names = {t if isinstance(t, str) else next(iter(t)) for t in tests}
                columns[column["name"]] = {"not_null": "not_null" in names}
            models[model["name"]] = {"yaml_file": str(path.relative_to(project)), "columns": columns}
    return models


def render(name: str, project: Path, seen: tuple[str, ...] = (), dialect: str = "redshift") -> str:
    """Render a model or seed reference into DuckDB-runnable SQL.

    Jinja is rendered with a minimal dbt context: ref(), var() with its default,
    config() and is_incremental(). Anything else (macros, source()) fails and the
    model has to be profiled against the warehouse instead.
    """
    if name in seen:
        raise ValueError(f"ref cycle at {name}")
    seeds = list((project / "seeds").rglob(f"{name}.csv"))
    if seeds:
        return f"read_csv_auto('{seeds[0]}', header=true)"
    sql_files = list((project / "models").rglob(f"{name}.sql"))
    if not sql_files:
        raise ValueError(f"cannot resolve ref('{name}')")
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    context = {
        "ref": lambda ref_name: render(ref_name, project, (*seen, name), dialect),
        "var": lambda _key, default=None: default,
        "config": lambda **_kw: "",
        "is_incremental": lambda: False,
    }
    try:
        sql = env.from_string(sql_files[0].read_text()).render(**context)
    except jinja2.UndefinedError as exc:
        raise ValueError(f"model {name} uses Jinja beyond ref/var/config ({exc}); profile it against the warehouse") from exc
    sql = sqlglot.transpile(sql, read=dialect, write="duckdb")[0]
    return f"({sql})"


def column_stats(con: duckdb.DuckDBPyConnection, relation: str, column: str, dtype: str) -> dict:
    q = f'"{column}"'
    numeric = any(t in dtype for t in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMERIC", "HUGEINT"))
    empty = f"{q} is null" + ("" if numeric else f" or trim(cast({q} as varchar)) = ''")
    stats = dict(zip(
        ["rows", "nulls", "distinct"],
        con.sql(f"select count(*), sum(case when {empty} then 1 else 0 end), count(distinct {q}) from {relation}").fetchone(),
    ))
    stats["dtype"] = dtype
    stats["numeric"] = numeric
    if numeric:
        stats["negatives"], stats["min"], stats["max"] = con.sql(
            f"select sum(case when {q} < 0 then 1 else 0 end), min({q}), max({q}) from {relation}"
        ).fetchone()
        stats["min"], stats["max"] = float(stats["min"] or 0), float(stats["max"] or 0)
        stats["looks_like_period"] = bool(
            stats["min"] >= 190001 and stats["max"] <= 299912 and "INT" in dtype
        )
    if stats["distinct"] <= LOW_CARDINALITY:
        stats["values"] = [
            {"value": v, "rows": n}
            for v, n in con.sql(
                f"select cast({q} as varchar), count(*) from {relation} group by 1 order by 2 desc, 1"
            ).fetchall()
        ]
    return {k: v for k, v in stats.items() if v is not None}


def parallel_versions(con, relation: str, column: str, others: list[str], distinct: int) -> float | None:
    """rows / distinct(other grain columns): ~= distinct when each row repeats once per value."""
    if distinct < 2 or not others:
        return None
    cols = ", ".join(f'"{c}"' for c in others)
    rows, groups = con.sql(f"select count(*), count(distinct ({cols})) from {relation}").fetchone()
    return round(rows / groups, 2) if groups else None


def repeats_per(con, relation: str, measure: str, entities: dict, _columns: dict, described) -> str | None:
    """Return the foreign entity whose rows repeat this measure (a fan-out copy).

    A measure is a repeated copy when every value of some coarser foreign key
    carries exactly one measure value, but that key repeats across rows. Summing
    it on this table multiplies the true amount.
    """
    present = {c for c, *_ in described}
    rows = con.sql(f"select count(*) from {relation}").fetchone()[0]
    for name, info in entities.items():
        col = info["column"]
        if info["type"] == "primary" or col not in present:
            continue
        keys, pairs = con.sql(
            f'select count(distinct "{col}"), count(distinct ("{col}", "{measure}")) from {relation}'
        ).fetchone()
        if keys and keys == pairs and keys < rows * 0.9:
            return name
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--config", help="scenario config (manifest list and validate.dialect are used here)")
    parser.add_argument("--dialect", help="warehouse SQL dialect; default validate.dialect or redshift")
    parser.add_argument("--out")
    args = parser.parse_args()
    project = Path(args.project).resolve()
    config = yaml.safe_load(Path(args.config).read_text()) if args.config else {}
    manifest_path = find_manifest(project, args.manifest, config)
    # Warehouse SQL dialect of the dbt models (sqlglot name: redshift, snowflake, bigquery, postgres, ...).
    dialect = args.dialect or config.get("validate", {}).get("dialect", "redshift")
    manifest = json.loads(manifest_path.read_text())
    yaml_models = model_yaml(project)
    con = duckdb.connect()

    result: dict = {"project": str(project), "manifest": str(manifest_path), "semantic_models": {}}
    for sm in manifest["semantic_models"]:
        model_name = (sm.get("node_relation") or {}).get("alias")
        entry: dict = {
            "model": model_name,
            "primary_entity": sm.get("primary_entity"),
            "agg_time_dimension": (sm.get("defaults") or {}).get("agg_time_dimension"),
            "yaml_file": yaml_models.get(model_name, {}).get("yaml_file"),
            "entities": {},
            "dimensions": {},
            "columns": {},
        }
        for entity in sm.get("entities", []):
            entry["entities"][entity["name"]] = {"type": entity["type"], "column": entity.get("expr") or entity["name"]}
        for dim in sm.get("dimensions", []):
            info = {"type": dim["type"], "column": dim.get("expr") or dim["name"]}
            if dim["type"] == "time":
                info["granularity"] = (dim.get("type_params") or {}).get("time_granularity")
            entry["dimensions"][dim["name"]] = info
        if entry["agg_time_dimension"]:
            entry["time_grain"] = entry["dimensions"].get(entry["agg_time_dimension"], {}).get("granularity")

        try:
            relation = render(model_name, project, dialect=dialect)
            con.sql(f"create or replace temp view profiled as select * from {relation}")
            described = con.sql("describe profiled").fetchall()
        except Exception as exc:  # noqa: BLE001 - keep profiling other models
            entry["profile_error"] = str(exc)[:300]
            result["semantic_models"][sm["name"]] = entry
            continue

        semantic_columns = {v["column"] for v in entry["entities"].values()} | {
            v["column"] for v in entry["dimensions"].values()
        }
        grain_columns = [
            v["column"] for v in entry["entities"].values() if v["type"] != "primary"
        ] + [v["column"] for v in entry["dimensions"].values()]
        yaml_columns = yaml_models.get(model_name, {}).get("columns", {})
        for column, dtype, *_ in described:
            stats = column_stats(con, "profiled", column, dtype)
            stats["not_null_test"] = yaml_columns.get(column, {}).get("not_null", False)
            stats["in_semantic_layer"] = column in semantic_columns
            if column in semantic_columns and stats.get("values"):
                others = [c for c in grain_columns if c != column]
                stats["parallel_versions"] = parallel_versions(con, "profiled", column, others, stats["distinct"])
            if not stats["in_semantic_layer"]:
                exact_numeric = stats["numeric"] and not any(t in dtype for t in ("DECIMAL", "DOUBLE", "FLOAT"))
                if stats["distinct"] == stats["rows"] and stats["rows"] > 1 and (exact_numeric or not stats["numeric"]):
                    stats["proposed_role"] = "row_id"
                elif stats.get("looks_like_period"):
                    stats["proposed_role"] = "period_label"
                elif stats["numeric"]:
                    stats["proposed_role"] = "measure"
                    repeat = repeats_per(con, "profiled", column, entry["entities"], entry["columns"], described)
                    if repeat:
                        stats["repeats_per_entity"] = repeat
                else:
                    stats["proposed_role"] = "unmodelled_attribute"
            entry["columns"][column] = stats
        result["semantic_models"][sm["name"]] = entry

    # A repeated measure is only a fan-out copy when the entity's own semantic
    # model (where it is primary) holds the same column at its native grain.
    # Otherwise it is a value allocated to that grain (e.g. attributed revenue).
    native_columns = {
        e["primary_entity"]: set(e["columns"]) for e in result["semantic_models"].values() if e.get("primary_entity")
    }
    for entry in result["semantic_models"].values():
        for column, stats in entry["columns"].items():
            entity = stats.get("repeats_per_entity")
            if entity and column not in native_columns.get(entity, set()):
                del stats["repeats_per_entity"]

    result["existing_metrics"] = [
        {"name": m["name"], "type": m["type"], "label": m.get("label")} for m in manifest["metrics"]
    ]
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"profile written: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
