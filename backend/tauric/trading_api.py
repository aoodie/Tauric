from fastapi import APIRouter

from . import capital, paper, strategies
from .auth import CurrentOwner, Db
from .capital_contracts import CapitalChange, CapitalCreate, CapitalVersionView, CapitalView
from .schemas import ResourceRef, ResourceView
from .strategy_contracts import DeploymentControl, DeploymentControlView
from .trading_contracts import (
    DeploymentCreate,
    DeploymentView,
    GovernanceReview,
    PaperAccountCreate,
    PaperAccountView,
    PaperAction,
    PaperHalt,
    PaperPolicyChange,
    PaperState,
    ProposalCreate,
    TradingRecordView,
)

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


@router.get("", response_model=PaperState)
def state(db: Db, owner: CurrentOwner):
    return paper.state(db, owner)


@router.post("/accounts", response_model=PaperAccountView, status_code=201)
def create_account(body: PaperAccountCreate, db: Db, owner: CurrentOwner):
    return paper.create_account(db, owner, body)


@router.put("/accounts/{account_id}/policy", response_model=PaperAccountView)
def policy(account_id: str, body: PaperPolicyChange, db: Db, owner: CurrentOwner):
    return paper.change_policy(db, owner, account_id, body)


@router.put("/accounts/{account_id}/halt", response_model=PaperAccountView)
def halt(account_id: str, body: PaperHalt, db: Db, owner: CurrentOwner):
    return paper.halt(db, owner, account_id, body)


@router.post("/capital", response_model=CapitalView, status_code=201)
def create_capital(body: CapitalCreate, db: Db, owner: CurrentOwner):
    return capital.save(db, owner, body)


@router.put("/capital/{scope_id}", response_model=CapitalView)
def change_capital(scope_id: str, body: CapitalChange, db: Db, owner: CurrentOwner):
    return capital.save(db, owner, body, scope_id)


@router.get("/capital/{scope_id}/history", response_model=list[CapitalVersionView])
def capital_history(scope_id: str, db: Db, owner: CurrentOwner):
    return capital.history(db, owner, scope_id)


@router.post("/deployments", response_model=DeploymentView, status_code=201)
def deployment(body: DeploymentCreate, db: Db, owner: CurrentOwner):
    return paper.create_deployment(db, owner, body)


@router.put("/deployments/{deployment_id}/control", response_model=DeploymentControlView)
def control(deployment_id: str, body: DeploymentControl, db: Db, owner: CurrentOwner):
    return strategies.change_control(db, owner, deployment_id, body)


@router.post("/proposals", response_model=TradingRecordView, status_code=201)
def proposal(body: ProposalCreate, db: Db, owner: CurrentOwner):
    return paper.propose(db, owner, body)


@router.post("/proposals/{proposal_id}/review", response_model=TradingRecordView, status_code=201)
def review(proposal_id: str, body: GovernanceReview, db: Db, owner: CurrentOwner):
    return paper.review(db, owner, proposal_id, body)


@router.post("/decisions/{decision_id}/plan", response_model=TradingRecordView, status_code=201)
def plan(decision_id: str, body: PaperAction, db: Db, owner: CurrentOwner):
    return paper.plan(db, owner, decision_id, body)


@router.post("/plans/{plan_id}/execute", response_model=TradingRecordView, status_code=201)
def execute(plan_id: str, body: PaperAction, db: Db, owner: CurrentOwner):
    return paper.execute(db, owner, plan_id, body)


@router.post("/positions/{position_id}/close", response_model=TradingRecordView, status_code=201)
def close(position_id: str, body: PaperAction, db: Db, owner: CurrentOwner):
    return paper.close(db, owner, position_id, body)


def resource(resource_type, resource_id, db, owner):
    ref = ResourceRef(type=resource_type, id=resource_id)
    if resource_type == "strategy_run":
        from . import strategy_execution

        row = strategy_execution.owned(db, owner, resource_id)
        return ResourceView(
            resource=ref,
            title="Executable paper strategy evaluation",
            data=strategy_execution.view(row).model_dump(mode="json"),
        )
    if resource_type == "trading_record":
        row = db.get(paper.TradingRecord, resource_id)
        if row and row.owner_id == owner.id:
            row = paper.record(db, owner, resource_id, row.kind)
            return ResourceView(
                resource=ref,
                title=f"Paper {row.kind}",
                data=TradingRecordView.model_validate(row).model_dump(mode="json"),
            )
    if resource_type == "paper_deployment":
        row = paper.deployment(db, owner, resource_id)
        return ResourceView(
            resource=ref,
            title="Paper strategy deployment",
            data={
                **paper.deployment_view(row, db).model_dump(mode="json"),
                "control_history": strategies.control_history(db, owner, row.id),
            },
        )
    if resource_type == "strategy_version":
        row = strategies.version(db, owner, resource_id)
        return ResourceView(
            resource=ref,
            title="Immutable strategy version",
            data=strategies.view(row).model_dump(mode="json"),
        )
    if resource_type == "capital_scope":
        row = capital.scope(db, owner, resource_id)
        acct = paper.account(db, owner, row.account_id)
        return ResourceView(
            resource=ref,
            title="Paper capital allocation",
            data={
                "current": next(
                    v.model_dump(mode="json")
                    for v in capital.views(db, acct)
                    if v.id == row.scope_id
                ),
                "history": [
                    v.model_dump(mode="json") for v in capital.history(db, owner, row.scope_id)
                ],
            },
        )
    return None
