from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def identifier() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class TradingViewOAuthFlow(Base):
    __tablename__ = "tradingview_oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    session_hash: Mapped[str] = mapped_column(ForeignKey("owner_sessions.token_hash"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_config: Mapped[str] = mapped_column(Text)


class ChatConfigVersion(Base):
    __tablename__ = "chat_config_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    revision: Mapped[int] = mapped_column(Integer, unique=True)
    encrypted_config: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    portfolio_id: Mapped[str | None] = mapped_column(String(36), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    user_text: Mapped[str] = mapped_column(Text)
    assistant_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    routing_fingerprint: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(20))
    lease_id: Mapped[str | None] = mapped_column(String(36))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatResearch(Base):
    __tablename__ = "chat_research"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("chat_turns.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    channel: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(64))
    research_key: Mapped[str] = mapped_column(String(64), index=True)
    charged: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    singleton: Mapped[bool] = mapped_column(Boolean, unique=True, default=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OwnerSession(Base):
    __tablename__ = "owner_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettingDefinition(Base):
    __tablename__ = "setting_definitions"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON)


class SettingHead(Base):
    __tablename__ = "setting_heads"
    __table_args__ = (UniqueConstraint("owner_id", "key", "scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    key: Mapped[str] = mapped_column(ForeignKey("setting_definitions.key"))
    scope: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class SettingVersion(Base):
    __tablename__ = "setting_versions"
    __table_args__ = (UniqueConstraint("head_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    head_id: Mapped[str] = mapped_column(ForeignKey("setting_heads.id"))
    revision: Mapped[int] = mapped_column(Integer)
    value: Mapped[dict | str | int | bool | None] = mapped_column(JSON, nullable=True)
    inherited: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LayoutVersion(Base):
    __tablename__ = "layout_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    revision: Mapped[int] = mapped_column(Integer)
    layout: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    interval: Mapped[str] = mapped_column(String(12))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    provider_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    profile: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    worker_slot: Mapped[str] = mapped_column(String(20), default="native")
    request: Mapped[dict] = mapped_column(JSON)
    progress: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AnalysisArtifact(Base):
    __tablename__ = "analysis_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"), unique=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkerStatus(Base):
    __tablename__ = "worker_status"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[str] = mapped_column(String(40))


class ModelConnectionVersion(Base):
    __tablename__ = "model_connection_versions"
    __table_args__ = (UniqueConstraint("profile", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    profile: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    encrypted_config: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MarketConnectionVersion(Base):
    __tablename__ = "market_connection_versions"
    __table_args__ = (UniqueConstraint("provider", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    provider: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    encrypted_config: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(12))
    starting_balance: Mapped[str] = mapped_column(String(40))
    request: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaperDeployment(Base):
    __tablename__ = "paper_deployments"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TradingRecord(Base):
    __tablename__ = "trading_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "request_id"),
        UniqueConstraint("kind", "consumes_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("paper_deployments.id"))
    kind: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(36))
    consumes_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GovernanceJob(Base):
    __tablename__ = "governance_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("trading_records.id"))
    profile: Mapped[str] = mapped_column(String(12))
    request: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    lease_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    context_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))


class GatewayStatus(Base):
    __tablename__ = "gateway_status"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CapitalScopeVersion(Base):
    __tablename__ = "capital_scope_versions"
    __table_args__ = (
        UniqueConstraint("scope_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    scope_id: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("paper_accounts.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("capital_scope_versions.id"))
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioPolicyVersion(Base):
    __tablename__ = "portfolio_policy_versions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    anchor_version_id: Mapped[str] = mapped_column(ForeignKey("capital_scope_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatPortfolioBinding(Base):
    __tablename__ = "chat_portfolio_bindings"
    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str | None] = mapped_column(String(36))
    anchor_version_id: Mapped[str | None] = mapped_column(ForeignKey("capital_scope_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioActionReceipt(Base):
    __tablename__ = "portfolio_action_receipts"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    turn_id: Mapped[str] = mapped_column(ForeignKey("chat_turns.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SharedAdmissionBudgetVersion(Base):
    __tablename__ = "shared_admission_budget_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioResearchPolicyVersion(Base):
    __tablename__ = "portfolio_research_policy_versions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    anchor_version_id: Mapped[str] = mapped_column(ForeignKey("capital_scope_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioResearchSchedule(Base):
    __tablename__ = "portfolio_research_schedules"
    portfolio_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    policy_version_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_research_policy_versions.id")
    )
    next_daily_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_cadence_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortfolioResearchRun(Base):
    __tablename__ = "portfolio_research_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        UniqueConstraint("portfolio_id", "trigger", "trigger_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    policy_version_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_research_policy_versions.id")
    )
    request_id: Mapped[str] = mapped_column(String(36))
    trigger: Mapped[str] = mapped_column(String(20))
    trigger_key: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortfolioResearchStep(Base):
    __tablename__ = "portfolio_research_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    run_id: Mapped[str] = mapped_column(ForeignKey("portfolio_research_runs.id"))
    step_index: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioResearchLink(Base):
    __tablename__ = "portfolio_research_links"
    __table_args__ = (UniqueConstraint("run_id", "instrument_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(ForeignKey("portfolio_research_runs.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    charged: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortfolioResearchDigest(Base):
    __tablename__ = "portfolio_research_digests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    run_id: Mapped[str] = mapped_column(ForeignKey("portfolio_research_runs.id"), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    strategy_id: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(12))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StrategyScheduleVersion(Base):
    __tablename__ = "strategy_schedule_versions"
    __table_args__ = (
        UniqueConstraint("deployment_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StrategySchedule(Base):
    __tablename__ = "strategy_schedules"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("strategy_schedule_versions.id"))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_tick_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_schedule_ticks.id"))


class StrategyScheduleTick(Base):
    __tablename__ = "strategy_schedule_ticks"
    __table_args__ = (UniqueConstraint("version_id", "due_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("strategy_schedule_versions.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_runs.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeploymentControlVersion(Base):
    __tablename__ = "deployment_control_versions"
    __table_args__ = (UniqueConstraint("deployment_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AutonomyPolicyVersion(Base):
    __tablename__ = "autonomy_policy_versions"
    __table_args__ = (
        UniqueConstraint("deployment_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AutonomyWorkflow(Base):
    __tablename__ = "autonomy_workflows"
    __table_args__ = (UniqueConstraint("deployment_id", "signal_key", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("autonomy_policy_versions.id"))
    initial_signal_id: Mapped[str] = mapped_column(ForeignKey("strategy_runs.id"))
    signal_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_job_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_jobs.id"))
    native_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_artifacts.id"))
    review_job_id: Mapped[str | None] = mapped_column(ForeignKey("governance_jobs.id"))
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    entry_fill_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    close_fill_id: Mapped[str | None] = mapped_column(ForeignKey("trading_records.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AutonomyEvent(Base):
    __tablename__ = "autonomy_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("paper_deployments.id"))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("autonomy_workflows.id"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerProfileVersion(Base):
    __tablename__ = "scanner_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    profile_id: Mapped[str] = mapped_column(String(36))
    strategy_version_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerRun(Base):
    __tablename__ = "scanner_runs"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    profile_version_id: Mapped[str] = mapped_column(ForeignKey("scanner_profile_versions.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerCandidate(Base):
    __tablename__ = "scanner_candidates"
    __table_args__ = (UniqueConstraint("owner_id", "context_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("market_snapshots.id"))
    context_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerMatch(Base):
    __tablename__ = "scanner_matches"
    __table_args__ = (UniqueConstraint("candidate_id", "profile_version_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("scanner_candidates.id"))
    profile_version_id: Mapped[str] = mapped_column(ForeignKey("scanner_profile_versions.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerEvent(Base):
    __tablename__ = "scanner_events"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("scanner_candidates.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScannerAnalysis(Base):
    __tablename__ = "scanner_analyses"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("scanner_candidates.id"))
    profile_version_id: Mapped[str] = mapped_column(ForeignKey("scanner_profile_versions.id"))
    strategy_version_id: Mapped[str | None] = mapped_column(ForeignKey("strategy_versions.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    research_key: Mapped[str] = mapped_column(String(64), index=True)
    charged: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
