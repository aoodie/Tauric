"""Owner-requested baseline research, run in the configured API container.

Only core research/qualification services are used. No orders or broker policy
changes are submitted. Provisional simulation inputs are not live risk limits.
"""

import json
import sys
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from tauric import (
    capital,
    market,
    paper,
    portfolios,
    qualification,
    research,
    strategies,
)
from tauric.capital_contracts import CapitalCreate
from tauric.database import engine
from tauric.models import Owner
from tauric.portfolio_contracts import PortfolioSave
from tauric.qualification_contracts import (
    QualificationAction,
    QualificationAssessmentRequest,
    QualificationFreeze,
)
from tauric.qualification_models import QualificationProtocol
from tauric.research_contracts import DatasetCapture
from tauric.trading_contracts import PaperAccountCreate, PaperHalt

STRATEGY = "ce1abe1b-321e-4709-8eb3-18fe3d987e4a"
BATCH = "owner-baseline-assessment:2026-10-07:v1:"
REASON = "Owner requested native Tauric assessment and advice on the saved breakout baseline; research only"


def key(name):
    return uuid5(NAMESPACE_URL, BATCH + name)


def prepare(db, owner):
    strategy = strategies.version(db, owner, STRATEGY)
    account = paper.create_account(
        db,
        owner,
        PaperAccountCreate(
            request_id=key("account"),
            name="Baseline research only (paused)",
            currency="USD",
            starting_balance="10000",
            policy={
                "risk_percent": "1",
                "max_total_risk_percent": "2",
                "max_exposure_percent": "50",
                "max_positions": 2,
                "fee_bps": "5",
                "slippage_bps": "5",
                "quote_max_age_seconds": 60,
                "decision_ttl_seconds": 900,
            },
            reason=REASON,
        ),
    )
    if not account.halted:
        paper.halt(
            db,
            owner,
            account.id,
            PaperHalt(expected_revision=account.revision, halted=True, reason=REASON),
        )
    scope = capital.save(
        db,
        owner,
        CapitalCreate(
            request_id=key("allocation"),
            account_id=account.id,
            kind="portfolio",
            name="Breakout baseline assessment",
            limits={
                "budget": "10000",
                "max_total_risk_percent": "2",
                "max_positions": 2,
            },
            reason=REASON,
        ),
    )
    current = portfolios.view(db, owner, scope.id)
    if not current.preferences:
        portfolios.save(
            db,
            owner,
            scope.id,
            PortfolioSave(
                request_id=key("mandate"),
                expected_revision=0,
                allocation_ids=[scope.id],
                preferences={
                    "mandate": "Assess the saved 15-minute breakout baseline for OANDA Forex and Alpaca stocks/ETFs. Tauric is mandatory approval authority. These are provisional costed research simulations, not live limits or trading authorization. Require representative out-of-sample and prospective evidence before activation, and disclose missing financing, fees, fill mechanics and data coverage.",
                    "research_objective": "balanced",
                },
                reason=REASON,
            ),
        )
    results = []
    for provider, instrument in (
        ("oanda", "oanda:fx:EUR_USD"),
        ("alpaca", "alpaca:equity:AAPL"),
    ):
        protocol = db.scalar(
            select(QualificationProtocol).where(
                QualificationProtocol.owner_id == owner.id,
                QualificationProtocol.request_id == str(key(provider + ":protocol")),
            )
        )
        if protocol:
            saved = qualification.view(db, owner, protocol)
        else:
            try:
                snapshot = market.refresh(
                    db, instrument, strategy.payload["parameters"]["rules"]["interval"]
                )
                dataset = research.capture(
                    db,
                    owner,
                    DatasetCapture(
                        request_id=key(provider + ":dataset"),
                        name=provider.upper() + " baseline research candles",
                        snapshot_id=snapshot.snapshot_id,
                        reason=REASON,
                    ),
                )
                bars = dataset.bar_count
                training = int(bars * 0.65)
                fx = provider == "oanda"
                costs = {
                    "asset_type": "fx" if fx else "equity",
                    "spread_bps": "0" if fx else "5",
                }
                if fx:
                    costs["financing_daily_bps"] = "1"
                saved = qualification.freeze(
                    db,
                    owner,
                    QualificationFreeze(
                        request_id=key(provider + ":protocol"),
                        portfolio_id=scope.id,
                        strategy_version_id=STRATEGY,
                        dataset_id=dataset.id,
                        name=provider.upper() + " breakout baseline diagnostic",
                        hypothesis="Assess the unchanged saved breakout baseline. The 65% training/remaining holdout split and 20-bar embargo are frozen before observing results. Costs are provisional conservative research inputs: FX uses historical bid/ask plus assumed daily financing of 1bp; stocks use assumed 5bp spread. They require broker-specific confirmation and longer independent data before live use. This short chart window cannot establish profitability or live stop/target execution.",
                        training_bars=training,
                        embargo_bars=20,
                        parameters={
                            "engine": "closed_bar_breakout_v1",
                            "strategy_version_id": STRATEGY,
                            "starting_balance": "10000",
                            "allocation_percent": "50",
                            "risk_percent": "1",
                            "quantity_step": "1",
                            "fee_bps": "0",
                            "slippage_bps": "5",
                            "asset_costs": costs,
                        },
                        thresholds={
                            "minimum_holdout_bars": max(30, bars - training - 20),
                            "minimum_closed_trades": 10,
                            "minimum_net_return_percent": "0",
                            "maximum_drawdown_percent": "10",
                        },
                        stresses=[
                            {
                                "name": "Higher transaction costs",
                                "fee_bps": "5",
                                "slippage_bps": "15",
                            }
                        ],
                        intended_mode="research_comparison",
                        assessment_validity_hours=24,
                        reason=REASON,
                    ),
                )
            except HTTPException as error:
                db.rollback()
                results.append(
                    {
                        "provider": provider,
                        "status": "unavailable",
                        "detail": str(error.detail),
                    }
                )
                continue
        if not saved.evaluation:
            saved = qualification.evaluate(
                db,
                owner,
                saved.id,
                QualificationAction(
                    request_id=key(provider + ":evaluate"), reason=REASON
                ),
            )
        if not saved.assessment_jobs:
            saved = qualification.assess(
                db,
                owner,
                saved.id,
                QualificationAssessmentRequest(
                    request_id=key(provider + ":native"), profile="local", reason=REASON
                ),
            )
        results.append(
            {
                "provider": provider,
                "protocol_id": saved.id,
                "dataset_id": saved.dataset_id,
                "native_jobs": [
                    {"id": row["job"]["id"], "status": row["job"]["status"]}
                    for row in saved.assessment_jobs
                ],
            }
        )
    print(
        json.dumps(
            {
                "research_account_id": account.id,
                "portfolio_id": scope.id,
                "results": results,
                "orders_submitted": 0,
            }
        )
    )


def status(db, owner):
    results = []
    for provider in ("oanda", "alpaca"):
        protocol = db.scalar(
            select(QualificationProtocol).where(
                QualificationProtocol.owner_id == owner.id,
                QualificationProtocol.request_id == str(key(provider + ":protocol")),
            )
        )
        if not protocol:
            continue
        saved = qualification.view(db, owner, protocol)
        jobs = saved.assessment_jobs
        if (
            jobs
            and jobs[0]["job"]["status"] in {"succeeded", "failed", "cancelled"}
            and not saved.reports
        ):
            saved = qualification.report(
                db,
                owner,
                saved.id,
                QualificationAction(
                    request_id=key(provider + ":report"), reason=REASON
                ),
            )
        results.append(saved.model_dump(mode="json"))
    # The frozen source archive is stored privately in the database; report only
    # summaries, linked jobs and the original native verdict here.
    public = []
    for result in results:
        public.append(
            {
                "provider": "oanda"
                if result["frozen"]["instrument_id"].startswith("oanda:")
                else "alpaca",
                "protocol_id": result["id"],
                "name": result["name"],
                "evaluation": result["evaluation"],
                "assessment_jobs": result["assessment_jobs"],
                "reports": result["reports"],
            }
        )
    print(json.dumps({"results": public, "orders_submitted": 0}, default=str))


def main():
    with Session(engine()) as db:
        owners = db.scalars(select(Owner)).all()
        if len(owners) != 1:
            raise SystemExit("Assessment requires an unambiguous owner context.")
        (status if len(sys.argv) > 1 and sys.argv[1] == "status" else prepare)(
            db, owners[0]
        )


if __name__ == "__main__":
    main()
