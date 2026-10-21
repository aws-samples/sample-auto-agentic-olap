#!/usr/bin/env bash
# On-demand Redshift Serverless warm-up (option A) -- thin launcher.
#
# WHY: this workgroup goes idle -> auto-suspends -> $0 while unused. The FIRST
# queries of a demo pay (1) a compute resume and (2) first-time COMPILATION of
# each query's segments via Serverless's external code cache, which on this
# workgroup intermittently takes MINUTES and is unrelated to data size -- so
# the MCP agent looks "stuck". Keeping it warm 24/7 is not an option: base is
# 32 RPU and the monthly usage limit is only 300 RPU-hours (~9.4h) before the
# workgroup auto-deactivates.
#
# INSTEAD: run this a few minutes BEFORE a demo. All the real work (generate the
# exact MetricFlow SQL the MCP run_query tool runs, then execute each via the
# Data API so its compiled segments land in the durable cache) happens in
# scripts/warmup_redshift.py. Doing it in Python avoids the fragile shell
# round-trip that used to mangle multi-KB SQL. The first pass is slow ON PURPOSE
# (it is compiling); a query that exceeds MAX_WAIT is NOT cancelled -- we stop
# waiting and let it finish caching in the background. After the demo the
# workgroup idles again on its own; cost = the few minutes of RPU you use.
#
# Usage:
#   scripts/warmup-redshift.sh
#   AWS_PROFILE=... WORKGROUP=medical-dev-wg MAX_WAIT=600 scripts/warmup-redshift.sh
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export WORKGROUP="${WORKGROUP:-medical-dev-wg}"
export DATABASE="${DATABASE:-medical_dw}"
export MAX_WAIT="${MAX_WAIT:-600}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/medical-data-service/backend"
VENV_PY="$BACKEND/.venv/bin/python"
MANIFEST="$BACKEND/semantic_manifest.json"

if [[ ! -x "$VENV_PY" ]]; then
  echo "backend venv not found at $VENV_PY (run 'uv sync' in $BACKEND)" >&2
  exit 1
fi
if [[ ! -f "$MANIFEST" ]]; then
  echo "semantic manifest missing at $MANIFEST -- running refresh-manifest.sh" >&2
  (cd "$BACKEND" && ./refresh-manifest.sh)
fi

cd "$BACKEND"
PYTHONPATH="$BACKEND" METRICFLOW_MANIFEST_PATH="$MANIFEST" \
  exec "$VENV_PY" "$REPO_ROOT/scripts/warmup_redshift.py"
