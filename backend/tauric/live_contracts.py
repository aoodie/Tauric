"""Readiness and prospective evidence do not themselves authorize an order."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from .broker_contracts import BrokerProvider
from .financial_contracts import Cost, Money
from .schemas import Contract


class ReadinessAction(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class ForwardRegister(ReadinessAction):
    provider: BrokerProvider
    strategy_version_id: UUID
    instrument_id: str = Field(min_length=1, max_length=120)
    minimum_days: int = Field(default=14, ge=1, le=365, strict=True)
    minimum_closed_trades: int = Field(default=20, ge=1, le=10000, strict=True)
    maximum_drawdown_percent: Money = "10"
    minimum_net_pnl_home: Cost = "0"
    maximum_signal_to_fill_seconds: int = Field(default=900, ge=1, le=86400, strict=True)
    maximum_adverse_slippage_bps: Cost = "25"
    risk_percent: Money = "1"

    @model_validator(mode="after")
    def ranges(self):
        if Decimal(self.maximum_drawdown_percent) > 100 or Decimal(self.risk_percent) > 100:
            raise ValueError("Risk and drawdown percentages must be at most 100.")
        return self


class ReadinessRecordView(Contract):
    id: str
    kind: str
    checksum: str
    payload: dict
    created_at: datetime


class BrokerReadinessView(Contract):
    provider: BrokerProvider
    environment: str
    connection_revision: int
    enabled: bool
    live_enabled: bool
    native_required: bool
    risk_authority: str
    forward_report_id: str | None
    entry_blockers: list[str] = Field(default_factory=list)


class TradingReadinessView(Contract):
    valuations: list[ReadinessRecordView]
    forward_protocols: list[ReadinessRecordView]
    forward_reports: list[ReadinessRecordView]
    brokers: list[BrokerReadinessView]
    valuation_worker_ready: bool
    forward_worker_ready: bool
    generated_at: datetime
