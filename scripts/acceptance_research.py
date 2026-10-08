"""One frozen EUR/USD research trial; read-only broker requests and no order authority.

Run with the application runtime and configured database. The deterministic trial
identity prevents duplicate protocols. Numerical failures are retained, never
repaired by lowering thresholds or selecting another historical window.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session
from tauric import (
    broker_execution,
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
from tauric.market_calendars import default_context, periods
from tauric.models import Owner
from tauric.portfolio_contracts import PortfolioSave
from tauric.qualification_contracts import QualificationAction, QualificationFreeze
from tauric.qualification_models import QualificationEvaluation, QualificationProtocol
from tauric.research_contracts import DatasetImport
from tauric.strategy_contracts import StrategyCreate
from tauric.trading_contracts import PaperAccountCreate, PaperHalt

INSTRUMENT = "oanda:fx:EUR_USD"
TRIAL = "eurusd-breakout-readiness-20261008-v1"
END = "2026-10-02T04:15:00Z"  # Ends before the previously observed diagnostic window.
STRATEGY = "ce1abe1b-321e-4709-8eb3-18fe3d987e4a"
PORTFOLIO = "eeab6d9f-47e4-459d-8565-894429f95ab1"


def request_id(owner, action):
    return uuid5(NAMESPACE_URL, TRIAL + ":" + owner.id + ":" + action)


def summary(result):
    return {key: result[key] for key in ("metrics", "final_equity", "return_percent", "max_drawdown_percent", "max_drawdown_amount")}


def prepare(db, owner):
    spec = market.instrument(db, INSTRUMENT)
    strategy, portfolio = candidate(db, owner)
    if not portfolio.preferences or strategy.payload["parameters"]["rules"]["interval"] != "15m":
        raise ValueError("The existing saved mandate and 15-minute strategy must be available.")
    existing = db.scalar(select(QualificationProtocol).where(
        QualificationProtocol.owner_id == owner.id,
        QualificationProtocol.request_id == str(request_id(owner, "protocol")),
    ))
    if existing:
        return existing
    config, _ = broker_execution.connection(db, "oanda")
    if config.environment != "practice":
        raise ValueError("Research trial requires the already configured practice connection.")
    with broker_execution.adapter("oanda", config) as broker:
        raw = broker.request("GET", "/v3/instruments/EUR_USD/candles", params={
            "price": "MBA", "granularity": "M15", "count": 5000, "to": END,
            "smooth": "false", "dailyAlignment": 17,
            "alignmentTimezone": "America/New_York", "weeklyAlignment": "Friday",
        })
    if raw.get("instrument") != "EUR_USD" or raw.get("granularity") != "M15":
        raise ValueError("Provider response instrument and granularity must match.")
    rows = raw.get("candles")
    if not isinstance(rows, list) or len(rows) != 5000:
        raise ValueError("Trial requires exactly 5,000 historical bars; do not shorten the holdout.")
    context, bars = default_context(spec), []
    for row in rows:
        if row.get("complete") is not True:
            raise ValueError("Research requires complete historical candles.")
        opening = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
        expected = list(periods(context, "15m", opening, opening))
        if not expected or type(row.get("volume")) is not int:
            raise ValueError("Provider sessions and tick activity must be valid.")
        sides = {name: {key: row[source][short] for key, short in
                 (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
                 for name, source in (("mid", "mid"), ("bid", "bid"), ("ask", "ask"))}
        bars.append({"open_time": opening, "close_time": expected[0][1],
                     **sides["mid"], "bid": sides["bid"], "ask": sides["ask"],
                     "volume": str(row["volume"]), "closed": True})
    dataset = research.import_dataset(db, owner, DatasetImport(
        request_id=request_id(owner, "dataset"), name="EUR/USD 5,000-bar frozen readiness trial",
        instrument_id=INSTRUMENT, interval="15m", candles=bars, data_context=context,
        source="OANDA v20 practice /v3/instruments/EUR_USD/candles; MBA, M15, unsmoothed, New York 17:00 alignment; collected 8 October 2026",
        reason="Owner authorized readiness plan; capture one longer independent research window without orders",
    ))
    body = QualificationFreeze(
        request_id=request_id(owner, "protocol"), portfolio_id=portfolio.id,
        strategy_version_id=strategy.id, dataset_id=dataset.id,
        name="EUR/USD unchanged breakout readiness trial",
        hypothesis="The unchanged 20-bar long breakout with 1.5x tick-activity filter, prior-low stop, 2R target and 10-bar channel exit retains nonnegative holdout returns under baseline and stressed costs. Freeze 3,250 training bars, 20 embargo bars and 1,730 holdout bars before evaluation. The data ends before the previously observed diagnostic window. No parameter search or relaxed criteria. Historical owner knowledge cannot be verified. FX breakout replay uses closed-bar exits and cannot certify broker intrabar protection. Financing is a conservative research debit assumption of 1bp per rollover day, not historical broker financing; actual financing and currency conversions require prospective evidence.",
        training_bars=3250, embargo_bars=20,
        parameters={"engine": "closed_bar_breakout_v1", "strategy_version_id": strategy.id,
                    "starting_balance": "10000", "allocation_percent": "50", "risk_percent": "1",
                    "quantity_step": "1", "fee_bps": "0", "slippage_bps": "5",
                    "asset_costs": {"asset_type": "fx", "spread_bps": "0", "financing_daily_bps": "1"}},
        thresholds={"minimum_holdout_bars": 1500, "minimum_closed_trades": 20,
                    "minimum_net_return_percent": "0", "maximum_drawdown_percent": "10"},
        stresses=[{"name": "Higher transaction costs", "fee_bps": "5", "slippage_bps": "15"}],
        intended_mode="research_comparison", assessment_validity_hours=168,
        reason="Freeze the owner-authorized first longer trial before observing results; no execution permission",
    )
    saved = qualification.freeze(db, owner, body)
    return db.get(QualificationProtocol, saved.id)


def candidate(db, owner):
    from tauric.models import StrategyVersion

    saved = db.get(StrategyVersion, STRATEGY)
    if saved and saved.owner_id == owner.id:
        return saved, portfolios.view(db, owner, PORTFOLIO)
    acct = paper.create_account(db, owner, PaperAccountCreate(
        request_id=request_id(owner, "research-account"), name="EUR/USD readiness research only",
        currency="USD", starting_balance="10000", reason="Create an isolated simulated research context; no broker allocation or trading route",
        policy={"risk_percent": "1", "max_total_risk_percent": "1", "max_exposure_percent": "50",
                "max_positions": 1, "fee_bps": "0", "slippage_bps": "5", "quote_max_age_seconds": 60,
                "decision_ttl_seconds": 900},
    ))
    if not acct.halted:
        paper.halt(db, owner, acct.id, PaperHalt(expected_revision=acct.revision, halted=True,
                  reason="Research-only context; simulated execution remains halted"))
    root = capital.save(db, owner, CapitalCreate(
        request_id=request_id(owner, "research-portfolio"), account_id=acct.id, kind="portfolio",
        name="EUR/USD frozen readiness research", limits={"budget": "10000", "max_total_risk_percent": "1", "max_positions": 1},
        reason="Research portfolio for frozen experiments; does not move broker capital",
    ))
    portfolio = portfolios.view(db, owner, root.id)
    if not portfolio.preferences:
        portfolio = portfolios.save(db, owner, root.id, PortfolioSave(
            request_id=request_id(owner, "research-mandate"), expected_revision=0,
            allocation_ids=[root.id], preferences={"mandate": "Evaluate the unchanged EUR/USD breakout with independent holdout and conservative costs. Research only; qualify before prospective promotion.", "research_objective": "balanced", "capital_mode": "fixed"},
            reason="Freeze the readiness research mandate without enabling conversational or execution permissions",
        ))
    strategy = strategies.save(db, owner, StrategyCreate(
        request_id=request_id(owner, "strategy"), name="EUR/USD breakout readiness baseline (unqualified)",
        parameters={"label": "Unchanged 15-minute breakout baseline", "description": "Exact executable rules of the existing demo baseline. One longer frozen research trial; no profitability or execution claim.",
                    "quantity_step": "1", "capital_limit": "100000", "risk_percent": "1",
                    "rules": {"engine": "closed_bar_breakout_v1", "interval": "15m", "entry_lookback": 20,
                              "minimum_volume_ratio": "1.5", "exit_lookback": 10, "take_profit_r": "2", "signal_ttl_seconds": 900}},
        reason="Save the existing baseline rules on the target host for the owner-authorized research trial; no route enabled",
    ))
    return strategies.version(db, owner, strategy.id), portfolio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with Session(engine()) as db:
        owners = db.scalars(select(Owner)).all()
        if len(owners) != 1:
            raise ValueError("Select an explicit owner before using this single-owner trial script.")
        owner = owners[0]
        protocol = prepare(db, owner)
        if args.evaluate:
            qualification.evaluate(db, owner, protocol.id, QualificationAction(
                request_id=request_id(owner, "evaluation"), reason="Evaluate the one frozen readiness trial; preserve every result",
            ))
            qualification.report(db, owner, protocol.id, QualificationAction(
                request_id=request_id(owner, "report"), reason="Record numerical results and explicitly missing native evidence",
            ))
        evaluation = db.scalar(select(QualificationEvaluation).where(QualificationEvaluation.protocol_id == protocol.id))
        view = qualification.view(db, owner, protocol)
        result = {"checked_at": datetime.now(UTC).isoformat(), "trial": TRIAL,
                  "protocol_id": protocol.id, "protocol_checksum": protocol.checksum,
                  "strategy_version_id": protocol.strategy_version_id, "dataset_id": protocol.dataset_id,
                  "training_range": protocol.payload["training_range"], "holdout_range": protocol.payload["holdout_range"],
                  "thresholds": protocol.payload["request"]["thresholds"], "execution_authority": False,
                  "native_assessment_requested": False, "reports": view.reports}
        if evaluation:
            result["evaluation_checksum"] = evaluation.checksum
            result["training"] = summary(evaluation.payload["training"])
            result["holdout"] = summary(evaluation.payload["holdout"])
            result["stresses"] = [{"scenario": s["scenario"], "result": summary(s["result"])} for s in evaluation.payload["stresses"]]
            result["holdout_exposure"] = evaluation.payload["holdout_exposure"]
        args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
