from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from . import services
from .auth import COOKIE_NAME, CurrentOwner, Db, csrf, login
from .broker_api import router as broker_router
from .chat_api import router as chat_router
from .config import SCHEMA_REVISION, runtime_config
from .defaults import default_layout
from .forex_connection import router as forex_connection_router
from .governance_api import resource as governance_resource
from .governance_api import router as governance_router
from .intelligence_api import router as intelligence_router
from .market_api import resource as market_resource
from .market_api import router as market_router
from .market_connections import connection_view as market_connection_view
from .market_connections import router as market_connections_router
from .models import AuditEvent, LayoutVersion, SettingDefinition, SettingVersion, Workspace, now
from .openclaw_api import router as openclaw_router
from .owner_settings import connection_view
from .owner_settings import router as owner_settings_router
from .portfolio_api import router as portfolio_router
from .portfolio_research_api import router as portfolio_research_router
from .qualification_api import router as qualification_router
from .research_api import resource as research_resource
from .research_api import router as research_router
from .scanner_api import resource as scanner_resource
from .scanner_api import router as scanner_router
from .schemas import (
    AuditPage,
    HealthView,
    LayoutChange,
    LayoutVersionView,
    LoginRequest,
    ResourceRef,
    ResourceView,
    RestoreRequest,
    Scope,
    SessionView,
    SettingChange,
    SettingVersionView,
    SettingView,
    WorkspaceCreate,
    WorkspaceView,
)
from .strategy_api import router as strategy_router
from .trading_api import resource as trading_resource
from .trading_api import router as trading_router
from .tradingview_connection import CALLBACK_PATH, OAUTH_COOKIE
from .tradingview_connection import router as tradingview_connection_router

app = FastAPI(title="Tauric platform", version="0.11.0", docs_url=None, redoc_url=None)
app.include_router(broker_router)
app.include_router(intelligence_router)
app.include_router(chat_router)
app.include_router(portfolio_router)
app.include_router(portfolio_research_router)
app.include_router(qualification_router)
app.include_router(market_connections_router)

app.include_router(forex_connection_router)
app.include_router(tradingview_connection_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.url.path == "/api/v1/tradingview/callback":
        request.state.tradingview_query = request.scope.get("query_string", b"")
        request.scope["query_string"] = b""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = (
        "no-referrer" if request.url.path == "/api/v1/tradingview/callback" else "same-origin"
    )
    response.headers["Cache-Control"] = "no-store"
    if request.url.path == CALLBACK_PATH:
        response.delete_cookie(
            OAUTH_COOKIE,
            path=CALLBACK_PATH,
            secure=runtime_config().secure_cookies,
            httponly=True,
            samesite="lax",
        )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic errors can include raw password inputs; return field/messages only.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{"loc": list(item["loc"]), "msg": item["msg"]} for item in error.errors()]
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request, error):
    return JSONResponse(
        status_code=503,
        content={"detail": "Storage is unavailable. Check system readiness and try again."},
    )


@app.get("/health/live", response_model=HealthView)
def live():
    return {"status": "ok"}


@app.get("/health/ready", response_model=HealthView, responses={503: {"model": HealthView}})
def ready(db: Db, response: Response):
    try:
        # A reachable but unmigrated database is not ready.
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        tables_exist = db.scalar(text("SELECT count(*) FROM setting_definitions"))
        if revision != SCHEMA_REVISION or not tables_exist:
            raise RuntimeError("Schema not ready")
    except (SQLAlchemyError, RuntimeError):
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}


@app.post("/api/v1/session", response_model=SessionView)
def sign_in(body: LoginRequest, request: Request, response: Response, db: Db):
    token, owner, stored = login(request, db, body.username, body.password)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=runtime_config().secure_cookies,
        samesite="strict",
        max_age=runtime_config().session_hours * 3600,
        path="/",
    )
    return SessionView(
        username=owner.username, csrf_token=csrf(token), expires_at=stored.expires_at
    )


@app.get("/api/v1/session", response_model=SessionView)
def session_info(request: Request, owner: CurrentOwner):
    return SessionView(
        username=owner.username,
        csrf_token=csrf(request.cookies[COOKIE_NAME]),
        expires_at=request.state.session.expires_at,
    )


@app.delete("/api/v1/session", status_code=204)
def sign_out(request: Request, response: Response, db: Db, owner: CurrentOwner):
    request.state.session.revoked_at = now()
    services.audit(db, owner, "session.signed_out", "owner", owner.id, "Owner signed out", {})
    db.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=runtime_config().secure_cookies,
        httponly=True,
        samesite="strict",
    )


@app.get("/api/v1/settings", response_model=list[SettingView])
def settings(db: Db, owner: CurrentOwner):
    keys = db.scalars(select(SettingDefinition.key).order_by(SettingDefinition.key)).all()
    return [services.setting_view(db, owner, key) for key in keys]


@app.put("/api/v1/settings/{key}", response_model=SettingView)
def update_setting(key: str, body: SettingChange, db: Db, owner: CurrentOwner):
    return services.change_setting(
        db, owner, key, body.scope, body.value, body.inherit, body.expected_revision, body.reason
    )


@app.get("/api/v1/settings/{key}/history", response_model=list[SettingVersionView])
def setting_history(
    key: str,
    db: Db,
    owner: CurrentOwner,
    scope: Scope = "user",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    services.definition(db, key)
    head = services.setting_head(db, owner, key, scope)
    if not head:
        return []
    return db.scalars(
        select(SettingVersion)
        .where(SettingVersion.head_id == head.id)
        .order_by(SettingVersion.revision.desc())
        .limit(limit)
        .offset(offset)
    ).all()


@app.post("/api/v1/settings/{key}/restore/{revision}", response_model=SettingView)
def restore_setting(
    key: str,
    revision: int,
    body: RestoreRequest,
    db: Db,
    owner: CurrentOwner,
    scope: Scope = "user",
):
    services.definition(db, key)
    head = services.setting_head(db, owner, key, scope)
    version = (
        db.scalar(
            select(SettingVersion).where(
                SettingVersion.head_id == head.id, SettingVersion.revision == revision
            )
        )
        if head
        else None
    )
    if not version:
        raise HTTPException(404, "This setting revision does not exist.")
    return services.change_setting(
        db,
        owner,
        key,
        scope,
        version.value,
        version.inherited,
        body.expected_revision,
        body.reason,
        restored_from=revision,
    )


@app.get("/api/v1/workspaces", response_model=list[WorkspaceView])
def workspaces(db: Db, owner: CurrentOwner):
    return [
        services.workspace_view(db, item)
        for item in db.scalars(
            select(Workspace).where(Workspace.owner_id == owner.id).order_by(Workspace.created_at)
        ).all()
    ]


@app.post("/api/v1/workspaces", response_model=WorkspaceView, status_code=201)
def new_workspace(body: WorkspaceCreate, db: Db, owner: CurrentOwner):
    return services.create_workspace(db, owner, body.name, body.clone_from)


@app.get("/api/v1/workspaces/{workspace_id}", response_model=WorkspaceView)
def workspace_detail(workspace_id: str, db: Db, owner: CurrentOwner):
    return services.workspace_view(db, services.get_workspace(db, owner, workspace_id))


@app.put("/api/v1/workspaces/{workspace_id}/layout", response_model=WorkspaceView)
def save_layout(workspace_id: str, body: LayoutChange, db: Db, owner: CurrentOwner):
    return services.change_layout(
        db, owner, workspace_id, body.layout, body.expected_revision, body.reason
    )


@app.post("/api/v1/workspaces/{workspace_id}/reset", response_model=WorkspaceView)
def reset_layout(workspace_id: str, body: RestoreRequest, db: Db, owner: CurrentOwner):
    return services.change_layout(
        db,
        owner,
        workspace_id,
        default_layout(),
        body.expected_revision,
        body.reason,
        action="workspace.reset",
    )


@app.get("/api/v1/workspaces/{workspace_id}/history", response_model=list[LayoutVersionView])
def layout_history(
    workspace_id: str,
    db: Db,
    owner: CurrentOwner,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    services.get_workspace(db, owner, workspace_id)
    return db.scalars(
        select(LayoutVersion)
        .where(LayoutVersion.workspace_id == workspace_id)
        .order_by(LayoutVersion.revision.desc())
        .limit(limit)
        .offset(offset)
    ).all()


@app.post("/api/v1/workspaces/{workspace_id}/restore/{revision}", response_model=WorkspaceView)
def restore_layout(
    workspace_id: str, revision: int, body: RestoreRequest, db: Db, owner: CurrentOwner
):
    services.get_workspace(db, owner, workspace_id)
    version = db.scalar(
        select(LayoutVersion).where(
            LayoutVersion.workspace_id == workspace_id, LayoutVersion.revision == revision
        )
    )
    if not version:
        raise HTTPException(404, "This layout revision does not exist.")
    return services.change_layout(
        db,
        owner,
        workspace_id,
        version.layout,
        body.expected_revision,
        body.reason,
        action="workspace.restored",
        restored_from=revision,
    )


@app.get("/api/v1/audit", response_model=AuditPage)
def audit_history(
    db: Db, owner: CurrentOwner, cursor: str | None = None, limit: int = Query(30, ge=1, le=100)
):
    return services.audit_page(db, owner, cursor, limit)


@app.get("/api/v1/resources/{resource_type}/{resource_id}", response_model=ResourceView)
def open_resource(resource_type: str, resource_id: str, db: Db, owner: CurrentOwner):
    ref = ResourceRef(type=resource_type, id=resource_id)
    if resource_type == "setting":
        view = services.setting_view(db, owner, resource_id)
        return ResourceView(
            resource=ref, title=view.definition.label, data=view.model_dump(mode="json")
        )
    if resource_type == "workspace":
        view = services.workspace_view(db, services.get_workspace(db, owner, resource_id))
        return ResourceView(resource=ref, title=view.name, data=view.model_dump(mode="json"))
    if resource_type == "audit":
        event = db.get(AuditEvent, resource_id)
        if event and event.owner_id == owner.id:
            return ResourceView(
                resource=ref,
                title=event.action,
                data=services.audit_view(event).model_dump(mode="json"),
            )
    if resource_type == "owner" and resource_id == owner.id:
        return ResourceView(
            resource=ref,
            title=owner.username,
            data={"username": owner.username, "created_at": owner.created_at.isoformat()},
        )
    if resource_type == "model_connection" and resource_id in {"cloud", "local"}:
        view = connection_view(db, resource_id)
        return ResourceView(
            resource=ref,
            title="Cloud AI connection"
            if resource_id == "cloud"
            else "External local AI connection",
            data=view.model_dump(mode="json"),
        )
    if resource_type == "market_connection" and resource_id in {"oanda", "alpaca"}:
        view = market_connection_view(db, resource_id)
        return ResourceView(
            resource=ref,
            title="OANDA market connection"
            if resource_id == "oanda"
            else "Alpaca market connection",
            data=view.model_dump(mode="json"),
        )
    if resource_type in {
        "trading_record",
        "paper_deployment",
        "capital_scope",
        "strategy_version",
        "strategy_run",
    }:
        found = trading_resource(resource_type, resource_id, db, owner)
        if found:
            return found
    if resource_type in {"scanner_candidate", "scanner_profile", "scanner_run"}:
        return scanner_resource(resource_type, resource_id, db, owner)
    if resource_type == "governance_job":
        return governance_resource(resource_id, db, owner)
    if resource_type in {"research_dataset", "research_run"}:
        return research_resource(resource_type, resource_id, db, owner)
    if resource_type in {"instrument", "analysis", "report", "market_snapshot"}:
        return market_resource(resource_type, resource_id, db, owner)
    raise HTTPException(404, "This resource does not exist.")


# Feature routes use the same authentication and resource contracts as the foundation.
app.include_router(market_router)
app.include_router(owner_settings_router)

app.include_router(trading_router)
app.include_router(strategy_router)

app.include_router(openclaw_router)
app.include_router(governance_router)

app.include_router(scanner_router)
app.include_router(research_router)
