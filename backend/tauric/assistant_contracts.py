"""Owner assistant management uses the same contracts as the workstation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .governance_contracts import GovernanceEnqueue
from .portfolio_contracts import ChatIdentity
from .quant_contracts import ExperimentCreate, HistoricalDatasetCreate, SessionReportCreate
from .research_contracts import DatasetCapture, ReplayCreate
from .scanner_contracts import CandidateAction, CandidateEscalate
from .schemas import Contract
from .strategy_contracts import StrategyRevise, StrategyRunRequest
from .trading_contracts import PaperAction, ProposalCreate

Operation = Literal[
    "save_scanner",
    "run_scanner",
    "candidate_action",
    "research_candidate",
    "propose_high_conviction_candidates",
    "create_strategy",
    "revise_strategy",
    "capture_dataset",
    "run_backtest",
    "retrieve_history",
    "session_research",
    "quant_experiment",
    "save_research_note",
    "prepare_upgrade",
    "stage_upgrade",
    "evaluate_paper_strategy",
    "propose_paper_strategy",
    "request_paper_review",
    "plan_paper_entry",
    "execute_paper_plan",
    "close_paper_position",
    "capture_broker_valuation",
    "register_broker_forward",
    "evaluate_broker_forward",
    "check_broker_account",
    "save_broker_policy",
    "save_broker_route",
    "assess_broker_setup",
    "request_broker_review",
    "submit_broker_entry",
    "close_broker_position",
    "reconcile_broker_order",
    "cancel_broker_order",
    "refresh_broker_position",
    "reconcile_broker_close",
    "repair_broker_protection",
]


class CandidateManagement(CandidateAction):
    candidate_id: UUID


class CandidateResearch(CandidateEscalate):
    candidate_id: UUID


class OpportunityBriefRequest(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=10, ge=1, le=25, strict=True)


class StrategyRevision(StrategyRevise):
    strategy_id: UUID


class PaperStrategyEvaluation(StrategyRunRequest):
    backtest_id: UUID


class PaperStrategyProposal(ProposalCreate):
    backtest_id: UUID
    native_artifact_id: UUID
    strategy_run_id: UUID


class PaperReviewRequest(GovernanceEnqueue):
    backtest_id: UUID


class PaperPlanRequest(PaperAction):
    decision_id: UUID
    backtest_id: UUID


class PaperExecutionRequest(PaperAction):
    plan_id: UUID
    backtest_id: UUID


class PaperCloseRequest(PaperAction):
    position_id: UUID


class SourceReference(Contract):
    path: str = Field(min_length=3, max_length=240)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ResearchNote(Contract):
    request_id: UUID
    title: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=20)
    source_evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    counter_evidence: list[str] = Field(min_length=1, max_length=20)
    test_plan: list[str] = Field(min_length=1, max_length=20)
    decision: Literal["test", "reject", "needs_data"]
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def bounded_notes(self):
        if any(
            not v.strip() or len(v) > 2000
            for values in (self.evidence, self.counter_evidence, self.test_plan)
            for v in values
        ):
            raise ValueError("Use concise, nonempty evidence and test steps.")
        return self


class UpgradeProposal(ResearchNote):
    files: list[SourceReference] = Field(min_length=1, max_length=30)
    changes: list[str] = Field(min_length=1, max_length=20)
    patch: str | None = Field(default=None, max_length=60000)

    @model_validator(mode="after")
    def bounded_changes(self):
        if len({v.path for v in self.files}) != len(self.files):
            raise ValueError("List each proposed file once.")
        if any(not v.strip() or len(v) > 2000 for v in self.changes):
            raise ValueError("Describe each proposed change concisely.")
        return self


class AssistantInvocation(ChatIdentity):
    operation: Operation
    parameters: dict = Field(default_factory=dict, max_length=40)
    reason: str = Field(min_length=3, max_length=500)
    effect_key: str | None = Field(default=None, min_length=3, max_length=80)


class AssistantRead(ChatIdentity):
    resource: Literal[
        "overview",
        "performance",
        "schemas",
        "scanner",
        "strategy",
        "candidate",
        "dataset",
        "backtest",
        "source",
        "receipt",
        "knowledge",
        "user_memory",
        "paper_deployment",
        "paper_state",
        "paper_record",
        "paper_review",
        "research_lab",
        "lab_record",
        "broker_readiness",
        "broker_state",
        "broker_routes",
        "broker_journal",
        "broker_reviews",
        "broker_setups",
    ] = "overview"
    operation: Operation | None = None
    resource_id: UUID | None = None
    path: str | None = Field(default=None, min_length=3, max_length=240)
    query: str = Field(default="", max_length=300)
    kind: Literal["strategy", "chat", "research", "preference", "wiki"] | None = None
    start_line: int = Field(default=1, ge=1, le=100000, strict=True)
    line_count: int = Field(default=160, ge=1, le=250, strict=True)


class AssistantReceipt(Contract):
    id: str
    turn_id: str | None
    operation: Operation
    title: str
    status: Literal["saved", "review_required"]
    result: dict
    checksum: str
    created_at: datetime


class AssistantState(Contract):
    authority: Literal["orchestrate_tauric_workflows"] = "orchestrate_tauric_workflows"
    tauric_authority: Literal["native_evidence_and_execution_gates"] = (
        "native_evidence_and_execution_gates"
    )
    code_changes_applied: Literal[False] = False
    receipts: list[AssistantReceipt]


ActivityStatus = Literal[
    "queued",
    "running",
    "waiting",
    "blocked",
    "retry_scheduled",
    "reconciling",
    "review_required",
    "completed",
    "failed",
    "cancelled",
]
ActivityCategory = Literal[
    "scan",
    "proposal",
    "research",
    "governance",
    "paper_trade",
    "upgrade",
    "chat",
    "operations",
]


class AssistantActivityLink(Contract):
    resource_type: str
    resource_id: str


class AssistantActivityItem(Contract):
    id: str
    timestamp: datetime
    updated_at: datetime | None = None
    actor: Literal["openclaw", "platform_worker", "owner", "unknown"]
    category: ActivityCategory
    title: str
    summary: str
    status: ActivityStatus
    operation: str
    resource_type: str
    resource_id: str
    request_id: str | None = None
    symbols: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
    links: list[AssistantActivityLink] = Field(default_factory=list)
    checksum: str | None = None
    verification: Literal["checksum_verified", "append_only", "observed_state"]


class AssistantActivityFeed(Contract):
    generated_at: datetime
    items: list[AssistantActivityItem]
    current_work: list[AssistantActivityItem]
    next_cursor: str | None = None
    poll_seconds: int = 10
    coverage: list[str] = Field(default_factory=list)


class ActivityObservationRequest(Contract):
    identities: list[str] = Field(min_length=1, max_length=100)


# Re-exported here so schema discovery always describes the actual service contracts.
RESEARCH_CONTRACTS = {
    "capture_dataset": DatasetCapture,
    "run_backtest": ReplayCreate,
    "retrieve_history": HistoricalDatasetCreate,
    "session_research": SessionReportCreate,
    "quant_experiment": ExperimentCreate,
}
