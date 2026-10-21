"""Medical sales data service.

- Remote MCP server (Streamable HTTP at /mcp) — guarded by a Cognito OAuth
  access token (MCP_OAUTH_ENABLED=true) or an API key (false).
- REST surface for the Vue portal — guarded by Cognito login.
- API key management (/api/admin/keys) — restricted to ADMIN_EMAIL.

No data-level permissions (no RLS/DDM): once past the door, all metrics are
visible.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.apikeys import api_key_store
from app.auth import User, is_admin, require_admin, require_user
from app.identity import Caller, reset_caller, set_caller
from app.mcp.server import OAUTH_ENABLED, mcp
from app.service import data_service, execution_enabled

_mcp_app = mcp.streamable_http_app()


class ApiKeyGuard:
    """Pure-ASGI guard for the MCP mount.

    Resolves the API key to its owning Cognito identity and stashes it in a
    contextvar for the duration of the request. Runs inline (same async task)
    so the contextvar propagates into the MCP tool functions — a FastAPI
    BaseHTTPMiddleware would hop tasks and lose it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        key = headers.get("x-api-key", "")
        if not key:
            auth = headers.get("authorization", "")
            if auth.startswith("Bearer "):
                key = auth.removeprefix("Bearer ").strip()
        record = api_key_store().resolve(key)
        if record is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"invalid or missing API key"}',
                }
            )
            return
        token = set_caller(
            Caller(
                email=record.get("created_by", "unknown"),
                key_id=record.get("id", ""),
                key_name=record.get("name", ""),
            )
        )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_caller(token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Medical Data Service (MCP)", version="1.0.0", lifespan=lifespan)


# The MCP endpoint lives at /mcp/ (the mounted app's route). A client that hits
# /mcp without the trailing slash otherwise falls through to the SPA catch-all
# and gets 405, which hides the 401 that drives OAuth discovery. Redirect it.
@app.api_route(
    "/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False
)
def _mcp_trailing_slash() -> RedirectResponse:
    return RedirectResponse("/mcp/", status_code=307)


# Remote MCP endpoint: connect a local agent to https://<host>/mcp
# - OAuth mode: FastMCP's own RequireAuthMiddleware validates Cognito tokens,
#   so mount the app directly.
# - API-key mode: wrap with the ApiKeyGuard which also carries the key owner's
#   identity into the tools.
if OAUTH_ENABLED:
    app.mount("/mcp", _mcp_app)
else:
    app.mount("/mcp", ApiKeyGuard(_mcp_app))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if OAUTH_ENABLED:
    # RFC 9728 Protected Resource Metadata. FastMCP registers this inside the
    # /mcp sub-app, but the spec (and our WWW-Authenticate header) points to the
    # ROOT path, so we serve it here to avoid the mount mismatch.
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
        # How the MCP endpoint is protected, so the portal shows the right
        # connection instructions.
        "mcp_auth_mode": "oauth" if OAUTH_ENABLED else "api_key",
        "mcp_agent_client_id": os.getenv("MCP_AGENT_CLIENT_ID"),
        "mcp_resource_url": os.getenv("MCP_RESOURCE_URL"),
    }


@app.get("/api/info")
def info(user: User = Depends(require_user)) -> dict:
    return {
        "name": "medical-data-service",
        # Lets the portal decide whether to show API-key management; the
        # admin endpoints still enforce this server-side.
        "is_admin": is_admin(user),
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
def api_metrics(_: User = Depends(require_user)) -> dict:
    return {
        "metrics": data_service().list_metrics(),
        "dimensions": data_service().list_dimensions(),
    }


class QueryRequest(BaseModel):
    metrics: list[str]
    group_by: list[str] = []
    limit: int = 1000


@app.post("/api/query/preview")
def api_preview(request: QueryRequest, _: User = Depends(require_user)) -> dict:
    try:
        return data_service().generate_sql(
            request.metrics, request.group_by, request.limit
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/query")
def api_query(request: QueryRequest, _: User = Depends(require_user)) -> dict:
    if not execution_enabled():
        raise HTTPException(status_code=503, detail="query execution is disabled")
    try:
        return data_service().run_query(
            request.metrics, request.group_by, request.limit
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# API key management — restricted to ADMIN_EMAIL.
# ---------------------------------------------------------------------------
class CreateKeyRequest(BaseModel):
    name: str = ""


@app.get("/api/admin/keys")
def list_keys(admin: User = Depends(require_admin)) -> list[dict]:
    return api_key_store().list()


@app.post("/api/admin/keys")
def create_key(request: CreateKeyRequest, admin: User = Depends(require_admin)) -> dict:
    return api_key_store().create(request.name, admin.email)


@app.delete("/api/admin/keys/{key_id}")
def revoke_key(key_id: str, admin: User = Depends(require_admin)) -> dict:
    if not api_key_store().revoke(key_id):
        raise HTTPException(status_code=404, detail="key not found or already revoked")
    return {"revoked": key_id}


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
