#!/usr/bin/env bash
# Entrypoint for the in-VPC dbt runner task.
#
# The warehouse is private, so this runs inside the private subnets. It reads the
# admin password from Secrets Manager, exports the env vars the profile expects,
# then builds the warehouse end to end.
set -euo pipefail

: "${REDSHIFT_HOST:?REDSHIFT_HOST is required}"
: "${REDSHIFT_SECRET_ARN:?REDSHIFT_SECRET_ARN is required}"
: "${AWS_REGION:=us-east-1}"

echo "Fetching database credentials from Secrets Manager"
SECRET_JSON="$(aws secretsmanager get-secret-value \
  --secret-id "$REDSHIFT_SECRET_ARN" \
  --region "$AWS_REGION" \
  --query SecretString --output text)"

REDSHIFT_USER="$(printf '%s' "$SECRET_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])')"
REDSHIFT_PASSWORD="$(printf '%s' "$SECRET_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["password"])')"
export REDSHIFT_USER REDSHIFT_PASSWORD REDSHIFT_HOST

echo "dbt debug"
dbt debug --target dev

echo "dbt seed"
dbt seed --full-refresh --target dev

echo "dbt run"
dbt run --target dev

echo "dbt test"
dbt test --target dev

echo "dbt build finished"
