"""Owner-authored fixed USDT allocations for paper portfolios and nested pots."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .financial_contracts import Money
from .schemas import Contract


class CapitalLimits(Contract):
    budget: Money
    max_total_risk_percent: Money
    max_positions: int = Field(ge=1, le=1000, strict=True)

    @model_validator(mode="after")
    def percentages(self):
        if Decimal(self.max_total_risk_percent) > 100:
            raise ValueError("Unlevered paper risk cannot exceed 100%.")
        return self


class CapitalCreate(Contract):
    request_id: UUID
    account_id: UUID
    kind: Literal["portfolio", "pot"]
    parent_id: UUID | None = None
    name: str = Field(min_length=3, max_length=80, pattern=r".*\S.*")
    limits: CapitalLimits
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def structure(self):
        if (self.kind == "portfolio") != (self.parent_id is None):
            raise ValueError("A portfolio is a root; a capital pot needs a parent.")
        if len(self.name.strip()) < 3:
            raise ValueError("Enter a meaningful allocation name.")
        return self


class CapitalChange(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    limits: CapitalLimits
    reason: str = Field(min_length=3, max_length=500)


class CapitalView(Contract):
    id: str
    version_id: str
    account_id: str
    parent_id: str | None
    kind: Literal["portfolio", "pot"]
    name: str
    revision: int
    limits: CapitalLimits
    allocated_to_children: str
    open_cost: str
    open_positions: int
    reason: str
    checksum: str
    created_at: datetime


class CapitalVersionView(Contract):
    id: str
    scope_id: str
    revision: int
    payload: dict
    checksum: str
    created_at: datetime
