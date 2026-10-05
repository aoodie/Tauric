"""Manual paper evaluations, evidence validation and attributed rule exits."""

import hashlib
import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import market, paper, services, strategies, strategy_rules
from .models import MarketSnapshot, Owner, StrategyRun, TradingRecord, now
from .strategy_contracts import BreakoutRules, StrategyRunView


def checked(row):
    if paper.checksum(row.payload) != row.checksum:
        paper.fail("Strategy run integrity failed.")
    return row


def view(row):
    return StrategyRunView.model_validate(checked(row))


def owned(db, owner, run_id):
    row = db.get(StrategyRun, str(run_id))
    if not row or row.owner_id != owner.id:
        paper.fail("This strategy run does not exist.", 404)
    return checked(row)


def rules_for(db, owner, dep):
    version_id = dep.payload.get("strategy_revision_id")
    if not version_id:
        paper.fail("Deploy a saved strategy version with executable rules first.", 422)
    version = strategies.version(db, owner, version_id)
    rules = version.payload["parameters"].get("rules")
    if not rules:
        paper.fail("This saved strategy has no executable rules.", 422)
    return version, BreakoutRules.model_validate(rules)


def snapshot_checked(db, snapshot_id):
    row = db.get(MarketSnapshot, snapshot_id) if snapshot_id else None
    if (
        not row
        or hashlib.sha256(json.dumps(row.data, sort_keys=True).encode()).hexdigest() != row.checksum
    ):
        paper.fail("Strategy market evidence integrity failed.")
    return row


def run(db, owner, body, *, commit=True, schedule_context=None, autonomous_context=None):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    dep = paper.deployment(db, owner, body.deployment_id)
    acct = paper.account(db, owner, dep.account_id)
    request = body.model_dump(mode="json")
    if schedule_context is not None:
        request["schedule_context"] = schedule_context
    if autonomous_context is not None:
        request["autonomous_context"] = autonomous_context
    previous = db.scalar(
        select(StrategyRun).where(
            StrategyRun.owner_id == owner.id, StrategyRun.request_id == str(body.request_id)
        )
    )
    if previous:
        if checked(previous).payload["request"] != request:
            paper.fail("This request identifier was already used for another strategy run.")
        return view(previous)
    version, rules = rules_for(db, owner, dep)
    _, _, positions, digest = paper.ledger(db, acct)
    attributed = [p for p in positions.values() if p.deployment_id == dep.id]
    control = strategies.control(db, dep)
    snapshot_id = None
    if len(attributed) > 1:
        result = {
            "action": "blocked",
            "reasons": ["Close legacy multiple positions explicitly before running these rules."],
        }
    elif not attributed and (acct.halted or not control.enabled):
        result = {
            "action": "blocked",
            "reasons": ["Account halt or deployment pause blocks new entries."],
        }
    else:
        try:
            feed = market.refresh(db, dep.instrument_id, rules.interval, commit=False)
            snapshot_id = feed.snapshot_id
            snapshot_checked(db, snapshot_id)
            if feed.instrument.asset_type in {"equity", "etf"}:
                from .stock_execution import evaluate as evaluate_stock

                result = evaluate_stock(feed, rules, attributed[0] if attributed else None)
            else:
                result = strategy_rules.evaluate(feed, rules, attributed[0] if attributed else None)
        except HTTPException as error:
            result = {"action": "blocked", "reasons": [str(error.detail)]}
    stamp = now()
    expiry = stamp + timedelta(seconds=rules.signal_ttl_seconds)
    if result.get("bar_close_time"):
        expiry = min(
            expiry,
            datetime.fromisoformat(result["bar_close_time"])
            + timedelta(seconds=market.INTERVAL_SECONDS[rules.interval], milliseconds=1),
        )
    payload = {
        "request": request,
        "reason": body.reason,
        **result,
        "rules": rules.model_dump(mode="json"),
        "strategy_checksum": version.checksum,
        "instrument_id": dep.instrument_id,
        "snapshot_id": snapshot_id,
        "control_revision": control.revision,
        "ledger_digest": digest,
        "expires_at": expiry.isoformat(),
        "mode": "paper",
        "run_mode": "autonomous"
        if autonomous_context
        else "scheduled"
        if schedule_context
        else "manual",
        "fill_model": "Rule levels are triggers. Explicit paper closes use the current observed 1m price with configured costs; no fill at the trigger level is assumed.",
    }
    payload["signal_key"] = paper.checksum(
        {
            "deployment_id": dep.id,
            "bar_close_time": result.get("bar_close_time"),
            "action": result["action"],
            "position_id": result.get("position_id"),
        }
    )
    row = StrategyRun(
        owner_id=owner.id,
        deployment_id=dep.id,
        strategy_version_id=version.id,
        snapshot_id=snapshot_id,
        request_id=str(body.request_id),
        action=result["action"],
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "strategy.rules_evaluated",
        "strategy_run",
        row.id,
        body.reason,
        {"action": row.action, "deployment_id": dep.id, "mode": "paper"},
    )
    if commit:
        db.commit()
    return view(row)


def validate(db, owner, dep, row, action):
    checked(row)
    version, rules = rules_for(db, owner, dep)
    if row.deployment_id != dep.id or row.strategy_version_id != version.id or row.action != action:
        paper.fail("The strategy signal does not match this deployment or action.")
    if row.payload["strategy_checksum"] != version.checksum or row.payload[
        "rules"
    ] != rules.model_dump(mode="json"):
        paper.fail("The pinned strategy rules cannot be verified.")
    if datetime.fromisoformat(row.payload["expires_at"]) <= now():
        paper.fail("The strategy signal expired. Run the rules again.")
    if (
        action == "enter"
        and row.payload["control_revision"] != strategies.control(db, dep).revision
    ):
        paper.fail("Deployment controls changed. Run the rules again.")
    acct = paper.account(db, owner, dep.account_id)
    _, _, positions, digest = paper.ledger(db, acct)
    if digest != row.payload["ledger_digest"]:
        paper.fail("The account ledger changed. Run the rules again.")
    attributed = [p for p in positions.values() if p.deployment_id == dep.id]
    if action == "enter" and attributed:
        paper.fail("This rule deployment already has an attributed position.")
    position = positions.get(row.payload.get("position_id")) if action == "exit" else None
    if action == "exit" and (not position or position.deployment_id != dep.id):
        paper.fail("The exit signal has no matching open attributed position.")
    snapshot_checked(db, row.snapshot_id)
    feed = market.view(db, dep.instrument_id, rules.interval)
    snapshot_checked(db, feed.snapshot_id)
    if feed.instrument.asset_type in {"equity", "etf"}:
        from .stock_execution import evaluate as evaluate_stock

        current = evaluate_stock(feed, rules, position)
    else:
        current = strategy_rules.evaluate(feed, rules, position)
    if current["action"] != action or current.get("bars") != row.payload.get("bars"):
        paper.fail("Closed-bar evidence changed or became unavailable. Run the rules again.")
    return row


def entry_signal(db, owner, dep, body):
    version_id = dep.payload.get("strategy_revision_id")
    rules = (
        strategies.version(db, owner, version_id).payload["parameters"].get("rules")
        if version_id
        else None
    )
    if not rules:
        if body.strategy_run_id:
            paper.fail("A manual strategy cannot consume an executable signal.", 422)
        return None
    if not body.strategy_run_id:
        paper.fail("Run this strategy's executable rules before creating a proposal.", 422)
    row = validate(db, owner, dep, owned(db, owner, body.strategy_run_id), "enter")
    if body.stop_price != row.payload["stop_price"]:
        paper.fail("The stop reference must match the saved executable signal.", 422)
    if db.scalar(
        select(TradingRecord.id).where(
            TradingRecord.deployment_id == dep.id,
            TradingRecord.kind == "proposal",
            TradingRecord.payload["strategy_signal_key"].as_string() == row.payload["signal_key"],
        )
    ):
        paper.fail(
            "This closed-bar entry signal already has a proposal. Review that proposal or wait for a new bar."
        )
    return row


def validate_proposal(db, dep, prop):
    owner = db.get(Owner, dep.owner_id)
    version_id = dep.payload.get("strategy_revision_id")
    rules = (
        strategies.version(db, owner, version_id).payload["parameters"].get("rules")
        if version_id
        else None
    )
    if not rules:
        return
    row = validate(db, owner, dep, owned(db, owner, prop.payload.get("strategy_run_id")), "enter")
    if (
        prop.payload.get("strategy_run_checksum") != row.checksum
        or prop.payload["stop_price"] != row.payload["stop_price"]
        or prop.payload.get("strategy_signal_key") != row.payload["signal_key"]
    ):
        paper.fail("The proposal's executable strategy lineage cannot be verified.")


def close(db, owner, run_id, body):
    row = owned(db, owner, run_id)
    dep = paper.deployment(db, owner, row.deployment_id)
    paper.account(db, owner, dep.account_id)
    request = {
        **body.model_dump(mode="json"),
        "position_id": row.payload.get("position_id"),
        "strategy_run_id": row.id,
    }
    existing = paper.replay(db, owner, "fill", body.request_id, request)
    if existing:
        return existing
    validate(db, owner, dep, row, "exit")
    return paper.close(db, owner, row.payload["position_id"], body, strategy_run=row)
