"""On-demand Redshift Serverless warm-up (option A) -- all in Python.

Generates the EXACT SQL the MCP run_query tool runs (MetricFlow's
LEFT-OUTER-JOIN + WHERE + subquery form) for the common metric/dimension
combos and executes each via the Redshift Data API, so their compiled query
segments land in Redshift's durable compile cache. The demo's real queries
then become cache hits (~7-8s) instead of a multi-minute cold compile.

Doing both steps in one process avoids the fragile shell round-trip (passing
multi-KB SQL through a bash array + tab-split), which broke on some bash
versions and fed malformed SQL to Redshift.

Env (all optional except AWS creds):
  AWS_PROFILE, AWS_REGION (default us-east-1),
  WORKGROUP (default medical-dev-wg), DATABASE (default medical_dw),
  MAX_WAIT  (seconds to WAIT -- not cancel -- before moving on; default 600).

Run via scripts/warmup-redshift.sh (sets PYTHONPATH + manifest), or directly:
  cd medical-data-service/backend
  PYTHONPATH=$PWD METRICFLOW_MANIFEST_PATH=$PWD/semantic_manifest.json \
    .venv/bin/python ../../scripts/warmup_redshift.py
"""

from __future__ import annotations

import logging
import os
import time

# MetricFlow logs noisy internal retry tracebacks (COUNT-type metrics retry at
# different optimization levels but still succeed). Silence it before import.
logging.disable(logging.CRITICAL)

import boto3  # noqa: E402
from app.service import data_service  # noqa: E402

REGION = os.getenv("AWS_REGION", "us-east-1")
WORKGROUP = os.getenv("WORKGROUP", "medical-dev-wg")
DATABASE = os.getenv("DATABASE", "medical_dw")
try:
    MAX_WAIT = int(os.getenv("MAX_WAIT", "600"))
except ValueError:
    MAX_WAIT = 600

# Dimensions the demo slices by most often. For each metric we warm the total
# (no group-by) plus each of these that is valid for that metric -- enough to
# compile every fact table's scan/join/aggregate segments without exploding
# into hundreds of permutations.
PREFERRED_DIMS = [
    "product__therapy_area",
    "sales_region__sales_region_name",
    "metric_time",
]


def build_queries(svc) -> list[tuple[str, str]]:
    """Return [(label, sql), ...] of real MetricFlow SQL, de-duplicated."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in svc.list_metrics():
        name = m["name"]
        valid = {d.strip() for d in (m.get("dimensions") or "").split(",") if d.strip()}
        group_bys: list[list[str]] = [[]]
        for dim in PREFERRED_DIMS:
            if dim in valid:
                group_bys.append([dim])
        for gb in group_bys:
            try:
                sql = svc.generate_sql([name], gb, 1000)["sql"]
            except Exception as exc:
                logging.getLogger(__name__).debug("skip %s %s: %s", name, gb, exc)
                continue
            if sql in seen:
                continue
            seen.add(sql)
            label = name + ("+" + ",".join(gb) if gb else "")
            out.append((label, sql))
    return out


def resolve_secret_arn(session) -> str:
    rss = session.client("redshift-serverless")
    ns = rss.get_workgroup(workgroupName=WORKGROUP)["workgroup"]["namespaceName"]
    arn = rss.get_namespace(namespaceName=ns)["namespace"].get("adminPasswordSecretArn")
    if not arn:
        raise SystemExit(f"could not resolve admin secret ARN for namespace '{ns}'")
    return arn


def run_stmt(data, secret_arn: str, sql: str, label: str) -> None:
    t0 = time.monotonic()
    stmt_id = data.execute_statement(
        WorkgroupName=WORKGROUP, Database=DATABASE, SecretArn=secret_arn, Sql=sql
    )["Id"]
    status = "SUBMITTED"
    while True:
        time.sleep(3)
        status = data.describe_statement(Id=stmt_id)["Status"]
        if status in ("FINISHED", "FAILED", "ABORTED"):
            break
        if time.monotonic() - t0 > MAX_WAIT:
            # Do NOT cancel: let it finish and cache its compiled segments in
            # the background. We just stop blocking on it.
            status = "RUNNING(bg)"
            break
    elapsed = time.monotonic() - t0
    if status == "FAILED":
        err = data.describe_statement(Id=stmt_id).get("Error", "")
        print(f"  {label:<42} [{status}] {elapsed:.0f}s  {err}")
    else:
        print(f"  {label:<42} [{status}] {elapsed:.0f}s")


def main() -> None:
    print(
        f"workgroup={WORKGROUP} database={DATABASE} "
        f"profile={os.getenv('AWS_PROFILE', '(default)')} region={REGION}"
    )
    svc = data_service()
    queries = build_queries(svc)
    if not queries:
        raise SystemExit("no warm-up SQL generated")
    print(f"generated {len(queries)} real MetricFlow queries to warm.")

    session = boto3.Session(region_name=REGION)
    secret_arn = resolve_secret_arn(session)
    data = session.client("redshift-data")

    print("warming up (first pass compiles segments and may take several minutes)...")
    start = time.monotonic()
    run_stmt(data, secret_arn, "select 1", "resume")
    for label, sql in queries:
        run_stmt(data, secret_arn, sql, label)
    total = time.monotonic() - start
    print(f"done in {total:.0f}s. Workgroup is hot; run your demo now.")


if __name__ == "__main__":
    main()
