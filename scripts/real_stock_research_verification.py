"""Owner-requested connected stock verification inside the existing API container.

Start once, then inspect durable jobs without retries. No financial writes or
connection changes; tokens and model traces are never included in evidence.
"""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from tauric import analysis, market, market_connections
from tauric.database import engine
from tauric.market_contracts import AnalysisRequest
from tauric.models import (
    AnalysisArtifact,
    AnalysisJob,
    AutonomyPolicyVersion,
    CapitalScopeVersion,
    Owner,
    PaperAccount,
    PaperDeployment,
    StrategyVersion,
    TradingRecord,
    now,
)

TARGET = Path("/tmp/tauric-real-stock-verification.json")
SYMBOLS = ("alpaca:equity:AAPL", "alpaca:etf:SPY")


def save(value):
    temporary = TARGET.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(TARGET)


def finances(db):
    result = {}
    for model in (
        PaperAccount,
        PaperDeployment,
        TradingRecord,
        CapitalScopeVersion,
        StrategyVersion,
        AutonomyPolicyVersion,
    ):
        rows = [
            {
                column.name: getattr(row, column.name)
                for column in model.__table__.columns
            }
            for row in db.scalars(select(model).order_by(model.id))
        ]
        result[model.__tablename__] = {
            "count": len(rows),
            "checksum": hashlib.sha256(
                json.dumps(rows, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
    return result


def inspect(db, value):
    complete = True
    states = []
    for item in value["research"]:
        job = db.scalar(
            select(AnalysisJob).where(AnalysisJob.request_id == item["request_id"])
        )
        if not job:
            complete = False
            states.append(
                {"instrument": item["instrument_id"], "status": "not_admitted"}
            )
            continue
        item.update(job_id=job.id, status=job.status, error=job.error)
        states.append(
            {
                "instrument": item["instrument_id"],
                "job_id": job.id,
                "status": job.status,
            }
        )
        if job.status != "succeeded":
            complete = False
            continue
        artifact = db.scalar(
            select(AnalysisArtifact).where(AnalysisArtifact.job_id == job.id)
        )
        assert artifact and artifact.payload["request"] == job.request
        assert (
            artifact.checksum
            == hashlib.sha256(
                json.dumps(artifact.payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        )
        assert artifact.payload["execution_authority"] is False
        assert artifact.payload["governance"] is None
        assert artifact.payload["upstream_revision"] == analysis.UPSTREAM_REVISION
        reports = artifact.payload["reports"]
        expected = {
            "market_report",
            "sentiment_report",
            "news_report",
            "final_trade_decision",
        }
        if job.request["instrument"]["asset_type"] == "equity":
            expected.add("fundamentals_report")
        else:
            assert "fundamentals" not in job.request["analysts"]
        assert all(
            isinstance(reports.get(k), str) and reports[k].strip() for k in expected
        )
        item.update(
            artifact_id=artifact.id,
            checksum=artifact.checksum,
            native_rating=artifact.payload["native_rating"],
            report_fields=list(reports),
            analysts=job.request["analysts"],
            trade_date=job.request["trade_date"],
            runtime_version=artifact.payload["version"],
            execution_authority=False,
            elapsed_seconds=round((job.finished_at - job.started_at).total_seconds()),
            source_scope=artifact.payload["data_context"],
        )
    value["financial_state_unchanged"] = finances(db) == value["financial_before"]
    value["previous_reports_unchanged"] = all(
        db.get(AnalysisArtifact, key).checksum == checksum
        for key, checksum in value["previous_reports"].items()
    )
    assert value["financial_state_unchanged"] and value["previous_reports_unchanged"]
    value["research_verified"] = complete
    value["checked_at"] = now().isoformat()
    save(value)
    print(
        json.dumps(
            {
                "research": states,
                "research_verified": complete,
                "feed_verified": value.get("feed", {}).get("quality") == "verified",
                "financial_state_unchanged": True,
                "previous_reports_unchanged": True,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "inspect"))
    args = parser.parse_args()
    with Session(engine()) as db:
        owner = db.scalar(select(Owner))
        assert owner
        if TARGET.exists():
            value = json.loads(TARGET.read_text())
        else:
            assert args.action == "start"
            value = {
                "started_at": now().isoformat(),
                "broker_orders": 0,
                "financial_before": finances(db),
                "previous_reports": {
                    a.id: a.checksum for a in db.scalars(select(AnalysisArtifact))
                },
                "research": [
                    {"instrument_id": symbol, "request_id": str(uuid4())}
                    for symbol in SYMBOLS
                ],
            }
            save(value)
        if args.action == "start":
            connection = market_connections.connection_view(db, "alpaca")
            value["connection"] = connection.model_dump(mode="json")
            if "feed" not in value:
                try:
                    value["feed"] = market_connections.check_connection(
                        "alpaca",
                        market_connections.MarketConnectionCheck(
                            expected_revision=connection.revision
                        ),
                        db,
                        owner,
                    ).model_dump(mode="json")
                except HTTPException as error:
                    value["feed"] = {"status": error.status_code, "error": error.detail}
                save(value)
            profiles = analysis.status(db).profiles
            chosen = next(
                (p.id for p in profiles if p.id == "local" and p.configured), None
            )
            if chosen is None:
                chosen = next((p.id for p in profiles if p.configured), None)
            assert chosen, "Configure a model connection before connected research."
            for item in value["research"]:
                if db.scalar(
                    select(AnalysisJob.id).where(
                        AnalysisJob.request_id == item["request_id"]
                    )
                ):
                    continue
                snapshot_id = None
                try:
                    feed = market.refresh(db, item["instrument_id"], "1d")
                    item["chart"] = {
                        "state": feed.state,
                        "snapshot_id": feed.snapshot_id,
                        "candles": len(feed.candles),
                        "gaps": feed.gaps,
                        "data_context": feed.data_context.model_dump(mode="json")
                        if feed.data_context
                        else None,
                    }
                    snapshot_id = feed.snapshot_id
                except HTTPException as error:
                    item["chart"] = {"status": error.status_code, "error": error.detail}
                job = analysis.enqueue(
                    db,
                    owner,
                    AnalysisRequest(
                        request_id=item["request_id"],
                        instrument_id=item["instrument_id"],
                        profile=chosen,
                        snapshot_id=snapshot_id,
                    ),
                )
                item.update(job_id=job.id, profile=chosen)
                save(value)
        inspect(db, value)


if __name__ == "__main__":
    main()
