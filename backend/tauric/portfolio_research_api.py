from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from . import admission_budget, portfolio_research
from .auth import CurrentOwner, Db
from .models import PortfolioResearchPolicyVersion
from .portfolio_research_contracts import (
    PortfolioResearchRequest,
    PortfolioResearchRunView,
    PortfolioResearchSave,
    PortfolioResearchState,
    SharedAdmissionBudgetSave,
    SharedAdmissionBudgetView,
)

router = APIRouter(prefix="/api/v1", tags=["portfolio research"])


@router.get("/research/admission-budget", response_model=SharedAdmissionBudgetView)
def budget(db: Db, owner: CurrentOwner):
    return admission_budget.view(db, owner)


@router.put("/research/admission-budget", response_model=SharedAdmissionBudgetView)
def save_budget(body: SharedAdmissionBudgetSave, db: Db, owner: CurrentOwner):
    return admission_budget.save(db, owner, body)


@router.get("/portfolios/{portfolio_id}/research", response_model=PortfolioResearchState)
def state(portfolio_id: UUID, db: Db, owner: CurrentOwner):
    return portfolio_research.state(db, owner, portfolio_id)


@router.put("/portfolios/{portfolio_id}/research", response_model=PortfolioResearchState)
def save(portfolio_id: UUID, body: PortfolioResearchSave, db: Db, owner: CurrentOwner):
    return portfolio_research.save(db, owner, portfolio_id, body)


@router.post(
    "/portfolios/{portfolio_id}/research/runs",
    response_model=PortfolioResearchRunView,
    status_code=202,
)
def run(portfolio_id: UUID, body: PortfolioResearchRequest, db: Db, owner: CurrentOwner):
    return portfolio_research.enqueue(db, owner, portfolio_id, body)


@router.get("/portfolios/{portfolio_id}/research/history", response_model=list[dict])
def history(portfolio_id: UUID, db: Db, owner: CurrentOwner):
    portfolio_research.portfolios.resolve(db, owner, portfolio_id)
    return [
        {
            "id": row.id,
            "revision": row.revision,
            "payload": portfolio_research.checked(row).payload,
            "checksum": row.checksum,
            "created_at": row.created_at.isoformat(),
        }
        for row in db.scalars(
            select(PortfolioResearchPolicyVersion)
            .where(
                PortfolioResearchPolicyVersion.owner_id == owner.id,
                PortfolioResearchPolicyVersion.portfolio_id == str(portfolio_id),
            )
            .order_by(PortfolioResearchPolicyVersion.revision.desc())
            .limit(50)
        )
    ]
