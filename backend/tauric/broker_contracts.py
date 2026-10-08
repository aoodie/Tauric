"""Broker-connected demo execution; amounts remain exact decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .financial_contracts import Money
from .market_contracts import AnalysisView
from .schemas import Contract

BrokerProvider = Literal["oanda", "alpaca"]


class BrokerAccountView(Contract):
    provider: BrokerProvider
    environment: Literal["practice", "paper", "live"]
    account_fingerprint: str
    currency: str
    cash: str
    equity: str
    buying_power: str
    trading_enabled: bool
    open_positions: int | None = None
    pending_orders: int | None = None
    observed_at: datetime

    realized_pnl_home: str | None = None
    fees_home: str | None = None
    financing_home: str | None = None


class BrokerInstrumentView(Contract):
    symbol: str
    tradable: bool
    quantity_step: str
    minimum_quantity: str
    maximum_quantity: str | None = None
    price_step: str
    margin_rate: str | None = None
    fractional: bool = False


class BrokerQuote(Contract):
    symbol: str
    bid: Money
    ask: Money
    observed_at: datetime
    tradable: bool
    feed: str | None = None
    quote_to_home_positive: str | None = None
    quote_to_home_negative: str | None = None
    quote_to_home_position: str | None = None

    @model_validator(mode="after")
    def spread(self):
        if Decimal(self.bid) > Decimal(self.ask):
            raise ValueError("Broker quote is crossed.")
        return self


class BrokerOrderRequest(Contract):
    client_order_id: str = Field(min_length=1, max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z][A-Z0-9_.-]*$")
    side: Literal["buy", "sell"]
    quantity: Money
    order_type: Literal["market", "limit", "stop"] = "market"
    limit_price: Money | None = None
    stop_price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    time_in_force: Literal["day", "gtc", "fok"] = "gtc"
    position_effect: Literal["open", "reduce"] = "open"

    @model_validator(mode="after")
    def prices(self):
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("A limit price belongs only to a limit order.")
        if (self.order_type == "stop") != (self.stop_price is not None):
            raise ValueError("A stop price belongs only to a stop order.")
        if self.order_type == "stop" and (self.stop_loss or self.take_profit):
            raise ValueError("Attached protection is supported on market or limit entry orders.")
        if self.position_effect == "reduce" and (self.stop_loss or self.take_profit):
            raise ValueError("A reducing order cannot attach new entry protection.")
        if self.stop_loss and self.take_profit:
            if (Decimal(self.stop_loss) < Decimal(self.take_profit)) != (self.side == "buy"):
                raise ValueError("Stop loss and take profit are in the wrong order for this side.")
        return self


class BrokerOrderView(Contract):
    provider: BrokerProvider
    broker_order_id: str
    client_order_id: str | None
    symbol: str | None
    side: Literal["buy", "sell"] | None
    requested_quantity: str | None
    filled_quantity: str
    average_fill_price: str | None
    status: Literal[
        "pending", "partially_filled", "filled", "cancelled", "rejected", "expired", "unknown"
    ]
    broker_status: str
    trade_ids: list[str] = Field(default_factory=list)
    child_order_ids: list[str] = Field(default_factory=list)
    observed_at: datetime

    @model_validator(mode="after")
    def valid_fills(self):
        filled = Decimal(self.filled_quantity)
        requested = (
            Decimal(self.requested_quantity) if self.requested_quantity is not None else None
        )
        if (
            not filled.is_finite()
            or filled < 0
            or (
                requested is not None
                and (not requested.is_finite() or requested <= 0 or filled > requested)
            )
        ):
            raise ValueError("Broker fill quantity is invalid.")
        if filled > 0 and (
            self.average_fill_price is None
            or not Decimal(self.average_fill_price).is_finite()
            or Decimal(self.average_fill_price) <= 0
        ):
            raise ValueError("A broker fill requires its actual positive execution price.")
        if self.status == "filled" and (requested is None or filled != requested):
            raise ValueError(
                "A filled broker order must account for its complete requested quantity."
            )
        return self


class BrokerPositionView(Contract):
    symbol: str
    side: Literal["long", "short"]
    quantity: str
    average_entry_price: str
    unrealized_pnl: str
    market_value: str | None = None


class BrokerConnectionStatus(Contract):
    provider: BrokerProvider
    configured: bool
    environment: str
    demo_environment: bool
    connection_revision: int
    account: BrokerAccountView | None = None
    positions: list[BrokerPositionView] = Field(default_factory=list)
    error: str | None = None


class BrokerPolicyChange(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    no_owner_limits: bool = Field(default=True, strict=True)
    maximum_risk_percent: Money | None = None
    maximum_drawdown_percent: Money | None = None
    live_acknowledged: bool = Field(default=False, strict=True)
    forward_report_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def owner_limits(self):
        limits = (self.maximum_risk_percent, self.maximum_drawdown_percent)
        if self.no_owner_limits and any(value is not None for value in limits):
            raise ValueError("Turn off no_owner_limits when setting risk limits.")
        if not self.no_owner_limits and any(value is None for value in limits):
            raise ValueError("Specify both maximum risk and maximum drawdown percentages.")
        if any(value is not None and Decimal(value) > 100 for value in limits):
            raise ValueError("Risk and drawdown percentages must be at most 100.")
        return self


class BrokerPolicyView(Contract):
    id: str
    provider: BrokerProvider
    revision: int
    settings: dict
    created_at: datetime


class BrokerOrderCreate(Contract):
    request_id: UUID
    expected_connection_revision: int = Field(ge=1, strict=True)
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z][A-Z0-9_.-]*$")
    side: Literal["buy", "sell"]
    quantity: Money
    order_type: Literal["market", "limit", "stop"] = "market"
    limit_price: Money | None = None
    stop_price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    time_in_force: Literal["day", "gtc", "fok"] = "gtc"
    position_effect: Literal["open", "reduce"] = "open"
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def order_contract(self):
        BrokerOrderRequest(
            client_order_id="validation",
            **self.model_dump(exclude={"request_id", "expected_connection_revision", "reason"}),
        )
        return self


class BrokerIntentView(Contract):
    id: str
    provider: BrokerProvider
    client_order_id: str
    status: str
    request: dict
    latest_order: BrokerOrderView | None
    detail: str
    created_at: datetime
    updated_at: datetime


class BrokerEventView(Contract):
    id: str
    sequence: int
    kind: str
    payload: dict
    checksum: str
    created_at: datetime


class BrokerExecutionState(Contract):
    worker_ready: bool
    policies: list[BrokerPolicyView]
    orders: list[BrokerIntentView]
    detail: str


class BrokerProtectionView(Contract):
    broker_order_id: str
    kind: Literal["stop_loss", "take_profit", "trailing_stop", "other"]
    status: str
    filled_quantity: str
    price: str | None = None
    remaining_quantity: str | None = None


class BrokerExecutionFill(Contract):
    fill_id: str = Field(min_length=1, max_length=256)
    broker_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Money
    price: Money
    occurred_at: datetime
    realized_pnl_home: str | None = None
    financing_home: str | None = None


class BrokerTradeView(Contract):
    trade_id: str
    client_trade_id: str | None
    symbol: str
    side: Literal["long", "short"]
    initial_quantity: Money
    remaining_quantity: str
    entry_price: Money
    status: Literal["OPEN", "CLOSED", "CLOSE_WHEN_TRADEABLE"]
    realized_pnl_home: str
    unrealized_pnl_home: str
    financing_home: str
    protections: list[BrokerProtectionView] = Field(default_factory=list)
    exit_fills: list[BrokerExecutionFill] = Field(default_factory=list)
    observed_at: datetime

    @model_validator(mode="after")
    def balance(self):
        remaining = Decimal(self.remaining_quantity)
        initial = Decimal(self.initial_quantity)
        if not remaining.is_finite() or not 0 <= remaining <= initial:
            raise ValueError("Broker trade quantity is inconsistent.")
        if (self.status == "CLOSED") != (remaining == 0):
            raise ValueError("Broker trade state and quantity disagree.")
        if (
            sum((Decimal(fill.quantity) for fill in self.exit_fills), Decimal(0))
            != initial - remaining
        ):
            raise ValueError("Broker trade exits do not reconcile to remaining quantity.")
        return self


class BrokerLifecycleView(Contract):
    entry_intent_id: str
    provider: BrokerProvider
    symbol: str
    side: Literal["long", "short"]
    currency: str
    status: Literal["unfilled", "open", "partially_closed", "closed", "unknown"]
    entry_quantity: str
    closed_quantity: str
    remaining_quantity: str
    broker_holding_quantity: str
    holding_check: Literal["covered", "shortfall", "closed"]
    notional_currency: str
    entry_notional: str
    closing_notional: str
    realized_gross_pnl: str | None
    broker_realized_pnl_home: str | None = None
    broker_financing_home: str | None = None
    protections: list[BrokerProtectionView] = Field(default_factory=list)
    entry_fills: list[BrokerExecutionFill] = Field(default_factory=list)
    exit_fills: list[BrokerExecutionFill] = Field(default_factory=list)
    trade_ids: list[str] = Field(default_factory=list)
    protection_status: Literal["covered", "unprotected", "incomplete", "not_required"] = (
        "incomplete"
    )
    observed_at: datetime
    detail: str

    @model_validator(mode="after")
    def balance(self):
        entered, closed, remaining, holding = map(
            Decimal,
            (
                self.entry_quantity,
                self.closed_quantity,
                self.remaining_quantity,
                self.broker_holding_quantity,
            ),
        )
        if any(
            not value.is_finite() or value < 0 for value in (entered, closed, remaining, holding)
        ):
            raise ValueError("Broker position quantities must be finite and nonnegative.")
        if entered != closed + remaining:
            raise ValueError("Attributed broker position quantities do not balance.")
        if sum((Decimal(fill.quantity) for fill in self.exit_fills), Decimal(0)) != closed:
            raise ValueError("Attributed broker exits do not reconcile to the closed quantity.")
        if self.status == "closed" and (remaining or not entered):
            raise ValueError("A closed position must have an entry and zero remaining quantity.")
        if self.holding_check != (
            "closed" if remaining == 0 else "covered" if holding >= remaining else "shortfall"
        ):
            raise ValueError("Broker holding coverage is inconsistent.")
        return self


class BrokerCloseAction(Contract):
    request_id: UUID
    quantity: Money
    reason: str = Field(min_length=3, max_length=500)


class BrokerCloseView(Contract):
    id: str
    entry_intent_id: str
    status: str
    quantity: str
    detail: str
    created_at: datetime


class BrokerJournalView(Contract):
    positions: list[BrokerLifecycleView]
    close_requests: list[BrokerCloseView]
    detail: str


class BrokerSetupRequest(Contract):
    request_id: UUID
    strategy_version_id: UUID
    instrument_id: str = Field(min_length=1, max_length=120)
    provider: BrokerProvider
    quantity: Money
    profile: Literal["cloud", "local"]
    reason: str = Field(min_length=3, max_length=500)


class NativeBrokerSetupVerdict(Contract):
    setup_id: UUID
    strategy_version_id: UUID
    evidence_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_id: str
    provider: BrokerProvider
    side: Literal["buy"]
    quantity: Money
    decision: Literal["approve", "reject", "incomplete"]
    rationale: str = Field(min_length=3, max_length=4000)
    limitations: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=20
    )


class NativeBrokerSetupAssessment(NativeBrokerSetupVerdict):
    adapter_version: Literal["tauric_broker_setup_v1"]
    source: Literal["native_tauric_broker_setup_review"]
    execution_authority: Literal[False] = False
    model_invocations: int = Field(ge=0, le=1, strict=True)


class BrokerSetupContext(Contract):
    adapter_version: Literal["tauric_broker_setup_v1"]
    evidence_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_input: dict


class BrokerSetupPreview(Contract):
    context: BrokerSetupContext
    entry_evidence_ready: bool
    signal_action: str
    reasons: list[str]
    expires_at: datetime
    quote: BrokerQuote | None
    planned_order: BrokerOrderRequest | None
    execution_authority: Literal[False] = False


class BrokerSetupView(Contract):
    job: AnalysisView
    native_artifact_id: str | None
    assessment: NativeBrokerSetupAssessment | None
    evidence_current: bool
    execution_authority: Literal[False] = False
    detail: str


class BrokerReviewRequest(Contract):
    request_id: UUID
    assessment_job_id: UUID
    profile: Literal["cloud", "local"]
    submit_on_allow: bool = False
    reason: str = Field(min_length=3, max_length=500)


class BrokerReviewResult(Contract):
    job_id: UUID
    context_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_job_id: UUID
    native_artifact_id: UUID
    strategy_version_id: UUID
    policy_id: UUID
    provider: BrokerProvider
    instrument_id: str
    side: Literal["buy"]
    quantity: Money
    outcome: Literal["allow", "reject", "defer"]
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")
    risk_scale: Money = "1"

    @model_validator(mode="after")
    def bounded_risk(self):
        if Decimal(self.risk_scale) > 1:
            raise ValueError("OpenClaw may reduce Tauric's approved ceiling, never increase it.")
        return self


class BrokerReviewedEntrySubmit(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class BrokerReviewDecisionView(Contract):
    id: str
    outcome: Literal["allow", "reject", "defer"]
    reason: str
    authority: Literal["demo_broker_entry", "live_broker_entry"]
    approved_order: BrokerOrderRequest
    risk_scale: Money = "1"
    expires_at: datetime
    created_at: datetime


class BrokerReviewView(Contract):
    id: str
    assessment_job_id: str
    profile: Literal["cloud", "local"]
    model: str
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    submit_on_allow: bool
    context_checksum: str
    decision: BrokerReviewDecisionView | None
    intent_id: str | None
    usage: dict[str, int]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class BrokerRouteChange(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    provider: BrokerProvider
    instrument_id: str = Field(min_length=1, max_length=120)
    strategy_version_id: UUID
    risk_percent: Money = "1"
    native_profile: Literal["cloud", "local"]
    review_profile: Literal["cloud", "local"]
    cadence_seconds: int = Field(ge=30, le=3600, strict=True)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def risk_range(self):
        if Decimal(self.risk_percent) > 100:
            raise ValueError("Risk percentage must be at most 100.")
        return self


class BrokerRouteView(Contract):
    id: str
    version_id: str
    revision: int
    settings: BrokerRouteChange
    next_due_at: datetime | None
    last_event: dict | None
    last_checked_at: datetime | None


class BrokerRouteWorkflowView(Contract):
    id: str
    route_id: str
    version_id: str
    status: str
    assessment_job_id: str
    review_job_id: str | None
    intent_id: str | None
    sizing: dict
    signal_bar_close: str
    created_at: datetime


class BrokerRoutesState(Contract):
    worker_ready: bool
    routes: list[BrokerRouteView]
    workflows: list[BrokerRouteWorkflowView]
    detail: str


class BrokerRouteEventView(Contract):
    id: str
    version_id: str
    workflow_id: str | None
    payload: dict
    created_at: datetime
