# One-off dbt runner, executed as a Fargate task inside the VPC.
#
# The warehouse is private (publicly_accessible = false), so dbt cannot reach it
# from a laptop. This image runs `dbt seed && dbt run && dbt test` from within
# the private subnets, which is also what a CI job would do.
#
# Build from the medical-olap-dbt directory:
#   docker build -t medical-dbt-runner .

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DBT_PROFILES_DIR=/app

WORKDIR /app

RUN pip install uv

# git stays in the image: it is needed to install MetricFlow from its pinned
# commit, and `dbt debug` also checks for git in PATH and fails without it.
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && uv pip install --system --no-cache -r requirements.txt awscli \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Profile reads REDSHIFT_HOST / REDSHIFT_USER / REDSHIFT_PASSWORD from the env.
COPY profiles.yml.example /app/profiles.yml

# The entrypoint fetches the DB password from Secrets Manager, then builds.
COPY scripts/run_dbt_build.sh /usr/local/bin/run_dbt_build.sh
RUN chmod +x /usr/local/bin/run_dbt_build.sh

ENTRYPOINT ["/usr/local/bin/run_dbt_build.sh"]
