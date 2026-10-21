#!/usr/bin/env bash
# Refresh the baked MetricFlow semantic manifest from the dbt project.
# Run this after changing the semantic layer (metrics/dimensions), then rebuild
# the Docker image so the MCP service exposes the updated catalog.
set -euo pipefail
cd "$(dirname "$0")"
SRC="../../medical-olap-dbt/target/semantic_manifest.json"
[ -f "$SRC" ] || { echo "not found: $SRC — run 'dbt parse' in medical-olap-dbt first"; exit 1; }
cp "$SRC" semantic_manifest.json
echo "manifest refreshed: $(wc -c < semantic_manifest.json) bytes"
