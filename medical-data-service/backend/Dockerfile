# The Vue frontend, the REST API and the MCP endpoint ship in one image and
# answer on one hostname, so the deployment needs a single certificate and no
# CORS configuration.
#
# Build from the medical-data-service directory, not from backend/:
#   docker build -f backend/Dockerfile -t medical-data-service .

FROM node:20-slim AS frontend

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ ./
# Same-origin API calls: the UI is served by the API, so a relative base is right.
ENV VITE_API_BASE=""
RUN npm run build


FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST_DIR=/app/static \
    METRICFLOW_MANIFEST_PATH=/app/semantic_manifest.json \
    QUERY_EXECUTION_ENABLED=true

WORKDIR /app

# git is needed only to install MetricFlow from its pinned commit, so it is
# added and removed in the same layer to keep it out of the final image.
# Dependencies come from uv.lock (exact versions), not the pyproject ranges.
RUN pip install uv==0.10.10

COPY backend/pyproject.toml backend/uv.lock ./
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    # MetricFlow is a large repo; HTTP/2 clones flake with "stream not closed
    # cleanly", so force HTTP/1.1 and a big buffer for a reliable fetch.
    && git config --global http.version HTTP/1.1 \
    && git config --global http.postBuffer 524288000 \
    && uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && apt-get purge -y git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY backend/app ./app
# The MetricFlow semantic manifest is a dbt build artifact. Bake a copy so the
# service is self-contained; refresh it (dbt parse) when the semantic layer
# changes. Source: medical-olap-dbt/target/semantic_manifest.json
COPY backend/semantic_manifest.json ./semantic_manifest.json
COPY --from=frontend /build/dist ./static

EXPOSE 8000

# The ALB terminates TLS and forwards over HTTP inside the VPC, so uvicorn must
# trust the X-Forwarded-* headers to know the client's real scheme and address.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
