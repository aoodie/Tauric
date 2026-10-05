from uuid import UUID

from fastapi import APIRouter

from . import portfolios
from .auth import CurrentOwner, Db
from .portfolio_contracts import PortfolioContext, PortfolioSave, PortfolioView

router = APIRouter(prefix="/api/v1/portfolios")


@router.get("", response_model=list[PortfolioView])
def index(db: Db, owner: CurrentOwner):
    return portfolios.list_portfolios(db, owner)


@router.get("/{portfolio_id}", response_model=PortfolioContext)
def read(portfolio_id: UUID, db: Db, owner: CurrentOwner):
    return portfolios.context(db, owner, portfolio_id)


@router.put("/{portfolio_id}", response_model=PortfolioView)
def save(portfolio_id: UUID, body: PortfolioSave, db: Db, owner: CurrentOwner):
    return portfolios.save(db, owner, portfolio_id, body)


@router.get("/{portfolio_id}/history", response_model=list[dict])
def history(portfolio_id: UUID, db: Db, owner: CurrentOwner):
    return portfolios.history(db, owner, portfolio_id)
