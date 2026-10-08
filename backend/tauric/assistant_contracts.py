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
]


class CandidateManagement(CandidateAction):
    candidate_id: UUID


class CandidateResearch(CandidateEscalate):
    candidate_id: UUID


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


# Re-exported here so schema discovery always describes the actual service contracts.
RESEARCH_CONTRACTS = {"capture_dataset": DatasetCapture, "run_backtest": ReplayCreate,
                      "retrieve_history": HistoricalDatasetCreate, "session_research": SessionReportCreate,
                      "quant_experiment": ExperimentCreate}
