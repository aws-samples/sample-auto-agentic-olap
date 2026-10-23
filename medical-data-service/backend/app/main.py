"""Medical sales data service.

- Remote MCP server (Streamable HTTP at /mcp).
- REST surface for the Vue portal (/api).

This application implements no authentication or authorization. The
Application Load Balancer validates Cognito tokens before a request reaches
/mcp or /api (jwt-validation listener rules in infra/terraform/alb.tf), and
the service security group accepts traffic from the load balancer only.

No data-level permissions (no RLS/DDM): once past the door, all metrics are
visible.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.mcp.server import mcp
from app.service import data_service, execution_enabled

_mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


# No interactive docs: the OpenAPI page would be the one unauthenticated
# description of the API surface.
app = FastAPI(
    title="Medical Data Service (MCP)",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# The MCP endpoint lives at /mcp/ (the mounted app's route). A client that hits
# /mcp without the trailing slash otherwise falls through to the SPA catch-all
# and gets 405, which hides the 401 that drives OAuth discovery. Redirect it.
@app.api_route(
    "/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False
)
def _mcp_trailing_slash() -> RedirectResponse:
    return RedirectResponse("/mcp/", status_code=307)


# Remote MCP endpoint: connect a local agent to https://<host>/mcp
app.mount("/mcp", _mcp_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# RFC 9728 Protected Resource Metadata: tells an MCP client which
# authorization server issues tokens for /mcp. It is discovery data, not an
# auth check; the load balancer enforces the token.
@app.get("/.well-known/oauth-protected-resource/mcp")
@app.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata() -> dict:
    return {
        "resource": os.environ["MCP_RESOURCE_URL"],
        "authorization_servers": [os.environ["COGNITO_ISSUER"]],
        "scopes_supported": [],
        "bearer_methods_supported": ["header"],
    }


@app.get("/api/config")
def public_config() -> dict:
    """Public: what the SPA needs before login (Cognito hosted-UI settings)."""
    return {
        "cognito_domain": os.getenv("COGNITO_DOMAIN"),
        "cognito_client_id": os.getenv("COGNITO_CLIENT_ID"),
        "mcp_agent_client_id": os.getenv("MCP_AGENT_CLIENT_ID"),
        "mcp_resource_url": os.getenv("MCP_RESOURCE_URL"),
    }


@app.get("/api/info")
def info() -> dict:
    return {
        "name": "medical-data-service",
        "mcp_path": "/mcp",
        "transport": "streamable-http",
        "execution_enabled": execution_enabled(),
        "tools": [
            {
                "name": "list_metrics",
                "description": "List all available metrics and the dimensions available for each",
            },
            {"name": "list_dimensions", "description": "List the dimensions available for grouping, with descriptions"},
            {"name": "preview_sql", "description": "Generate SQL only, without executing"},
            {
                "name": "run_query",
                "description": "Query Redshift using semantic-layer metrics and return the data",
            },
        ],
    }


@app.get("/api/metrics")
def api_metrics() -> dict:
    return {
        "metrics": data_service().list_metrics(),
        "dimensions": data_service().list_dimensions(),
    }


class QueryRequest(BaseModel):
    metrics: list[str]
    group_by: list[str] = []
    limit: int = 1000


@app.post("/api/query/preview")
def api_preview(request: QueryRequest) -> dict:
    try:
        return data_service().generate_sql(
            request.metrics, request.group_by, request.limit
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/query")
def api_query(request: QueryRequest) -> dict:
    if not execution_enabled():
        raise HTTPException(status_code=503, detail="query execution is disabled")
    try:
        return data_service().run_query(
            request.metrics, request.group_by, request.limit
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Vue frontend (built to ./static by the Docker image).
# ---------------------------------------------------------------------------
_FRONTEND_DIST = Path(
    os.getenv(
        "FRONTEND_DIST_DIR", str(Path(__file__).resolve().parent.parent / "static")
    )
)

if (_FRONTEND_DIST / "index.html").is_file():
    app.mount(
        "/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets"
    )

    def _index() -> FileResponse:
        return FileResponse(
            _FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return _index()

    @app.get("/{path:path}", include_in_schema=False)
    def serve_spa(path: str) -> FileResponse:
        candidate = (_FRONTEND_DIST / path).resolve()
        if candidate.is_file() and _FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return _index()
