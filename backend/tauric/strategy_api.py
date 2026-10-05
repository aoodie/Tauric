from fastapi import APIRouter

from . import autonomous, strategies, strategy_execution, strategy_scheduler
from .auth import CurrentOwner, Db
from .autonomy_contracts import AutonomyHistory, AutonomyPolicyChange, AutonomyPolicyView
from .strategy_contracts import (
    StrategyCreate,
    StrategyRevise,
    StrategyRunRequest,
    StrategyRunView,
    StrategyScheduleChange,
    StrategyScheduleHistory,
    StrategyScheduleView,
    StrategyVersionView,
)
from .trading_contracts import PaperAction, TradingRecordView

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyVersionView], response_model_exclude_none=True)
def versions(db: Db, owner: CurrentOwner):
    return strategies.versions(db, owner)


@router.post(
    "", response_model=StrategyVersionView, status_code=201, response_model_exclude_none=True
)
def create(body: StrategyCreate, db: Db, owner: CurrentOwner):
    return strategies.save(db, owner, body)


@router.post("/runs", response_model=StrategyRunView, status_code=201)
def run(body: StrategyRunRequest, db: Db, owner: CurrentOwner):
    return strategy_execution.run(db, owner, body)


@router.post("/runs/{run_id}/close", response_model=TradingRecordView, status_code=201)
def close(run_id: str, body: PaperAction, db: Db, owner: CurrentOwner):
    return strategy_execution.close(db, owner, run_id, body)


@router.put("/deployments/{deployment_id}/schedule", response_model=StrategyScheduleView)
def schedule(deployment_id: str, body: StrategyScheduleChange, db: Db, owner: CurrentOwner):
    return strategy_scheduler.save(db, owner, deployment_id, body)


@router.get("/deployments/{deployment_id}/schedule/history", response_model=StrategyScheduleHistory)
def schedule_history(deployment_id: str, db: Db, owner: CurrentOwner):
    return strategy_scheduler.history(db, owner, deployment_id)


@router.get(
    "/{strategy_id}/versions",
    response_model=list[StrategyVersionView],
    response_model_exclude_none=True,
)
def history(strategy_id: str, db: Db, owner: CurrentOwner):
    return strategies.versions(db, owner, strategy_id)


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionView,
    status_code=201,
    response_model_exclude_none=True,
)
def revise(strategy_id: str, body: StrategyRevise, db: Db, owner: CurrentOwner):
    return strategies.save(db, owner, body, strategy_id)


@router.put("/deployments/{deployment_id}/autonomy", response_model=AutonomyPolicyView)
def autonomy(deployment_id: str, body: AutonomyPolicyChange, db: Db, owner: CurrentOwner):
    return autonomous.save(db, owner, deployment_id, body)


@router.get("/deployments/{deployment_id}/autonomy/history", response_model=AutonomyHistory)
def autonomy_history(deployment_id: str, db: Db, owner: CurrentOwner):
    return autonomous.history(db, owner, deployment_id)
