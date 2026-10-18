"""Attributed broker position facts, durable closes and journal projections."""

from datetime import timedelta
from decimal import Decimal, localcontext
from hashlib import blake2b

from fastapi import HTTPException
from sqlalchemy import select, text

from . import broker_execution as execution
from . import broker_lifecycle_adapters as facts
from .broker_adapters import BrokerError
from .broker_contracts import (
    BrokerCloseView,
    BrokerJournalView,
    BrokerLifecycleView,
    BrokerOrderRequest,
)
from .broker_models import (
    BrokerCloseRequest,
    BrokerOrderEvent,
    BrokerOrderIntent,
    BrokerPositionCursor,
)
from .models import Owner, identifier, now

D = Decimal


def append(db, entry, kind, payload):
    history = execution.events(db, entry)
    if history and history[-1].kind == kind:
        previous, current = dict(history[-1].payload), dict(payload)
        previous.pop("observed_at", None)
        current.pop("observed_at", None)
        if previous == current:
            return
    db.add(
        BrokerOrderEvent(
            owner_id=entry.owner_id,
            intent_id=entry.id,
            sequence=(history[-1].sequence if history else 0) + 1,
            kind=kind,
            payload=payload,
            checksum=execution.checksum(payload),
        )
    )
    db.flush()


def latest(db, entry):
    event = next(
        (r for r in reversed(execution.events(db, entry)) if r.kind == "position_observed"), None
    )
    return BrokerLifecycleView.model_validate(event.payload["position"]) if event else None


def close_view(db, row):
    if row.checksum != execution.checksum(row.payload):
        raise HTTPException(409, "Broker close request integrity failed.")
    entry = execution.owned(db, db.get(Owner, row.owner_id), row.entry_intent_id)
    event = next(
        (
            r
            for r in reversed(execution.events(db, entry))
            if r.payload.get("close_request_id") == row.id
        ),
        None,
    )
    return BrokerCloseView(
        id=row.id,
        entry_intent_id=row.entry_intent_id,
        status=row.status,
        quantity=row.payload["request"]["quantity"],
        detail=event.payload.get("detail", "") if event else "",
        created_at=row.created_at,
    )


def ensure_cursor(db, entry):
    cursor = db.get(BrokerPositionCursor, entry.id)
    if not cursor:
        cursor = BrokerPositionCursor(
            entry_intent_id=entry.id,
            owner_id=entry.owner_id,
            status="unknown",
            updated_at=now() - timedelta(seconds=30),
        )
        db.add(cursor)
        db.flush()
    return cursor


def register_entry(db, entry, order):
    if entry.payload["order"]["position_effect"] == "open" and D(order.filled_quantity) > 0:
        ensure_cursor(db, entry)


def close_rows(db, entry):
    return list(
        db.scalars(
            select(BrokerCloseRequest)
            .where(BrokerCloseRequest.entry_intent_id == entry.id)
            .order_by(BrokerCloseRequest.created_at)
        )
    )


def close_submission(db, entry, request):
    event = next(
        (
            r
            for r in reversed(execution.events(db, entry))
            if r.kind == "close_submission_started"
            and r.payload.get("close_request_id") == request.id
        ),
        None,
    )
    return event.payload if event else None


def close_not_submitted(db, entry, request):
    return any(
        e.kind == "close_not_submitted" and e.payload.get("close_request_id") == request.id
        for e in execution.events(db, entry)
    )


def replacement_submissions(db, entry):
    return [
        event.payload
        for event in execution.events(db, entry)
        if event.kind == "protection_submission_started"
    ]


def alpaca_children(db, entry, broker, order):
    result = facts.alpaca_children(broker, order)
    for submission in replacement_submissions(db, entry):
        result.extend(facts.alpaca_replacement(broker, submission))
    return result


def collect(db, entry, broker):
    with localcontext() as context:
        context.prec = 120
        return _collect(db, entry, broker)


def _collect(db, entry, broker):
    account = broker.account()
    if account.account_fingerprint != entry.account_fingerprint:
        raise BrokerError("The broker position account differs from its recorded entry.")
    order = broker.order(entry.client_order_id)
    execution.verify_order(entry, order)
    entered = D(order.filled_quantity)
    closed, remaining, entry_notional, closing_notional = D(0), entered, D(0), D(0)
    entry_fills, exits, protections, trade_ids = [], [], [], []
    broker_pnl, financing = None, None
    if entered:
        if entry.provider == "oanda":
            if not order.trade_ids or len(set(order.trade_ids)) != len(order.trade_ids):
                raise BrokerError("The entry has no unambiguous broker trade lineage.")
            trades = [facts.oanda_trade(broker, trade_id) for trade_id in order.trade_ids]
            for trade in trades:
                if (
                    trade.client_trade_id != entry.client_order_id
                    or trade.symbol != order.symbol
                    or (trade.side == "long") != (order.side == "buy")
                ):
                    raise BrokerError("The OANDA trade does not belong to the recorded entry.")
            if sum((D(trade.initial_quantity) for trade in trades), D(0)) != entered:
                raise BrokerError("The OANDA trade quantities do not reconcile to the entry fill.")
            remaining = sum((D(trade.remaining_quantity) for trade in trades), D(0))
            closed = entered - remaining
            protections = [p for trade in trades for p in trade.protections]
            exits = [fill for trade in trades for fill in trade.exit_fills]
            trade_ids = [trade.trade_id for trade in trades]
            entry_notional = sum(
                (D(trade.initial_quantity) * D(trade.entry_price) for trade in trades), D(0)
            )
            closing_notional = sum((D(fill.quantity) * D(fill.price) for fill in exits), D(0))
            broker_pnl = format(sum((D(trade.realized_pnl_home) for trade in trades), D(0)), "f")
            financing = format(sum((D(trade.financing_home) for trade in trades), D(0)), "f")
        else:
            entry_fills = facts.alpaca_fills(broker, order)
            entry_notional = sum((D(fill.quantity) * D(fill.price) for fill in entry_fills), D(0))
            children = alpaca_children(db, entry, broker, order)
            for child, protection in children:
                protections.append(protection)
                if D(child.filled_quantity) > 0:
                    exits.extend(facts.alpaca_fills(broker, child))
            for request in close_rows(db, entry):
                submitted = close_submission(db, entry, request)
                if submitted and not close_not_submitted(db, entry, request):
                    close_order = broker.order(request.payload["client_order_id"])
                    verify_close_order(entry, request, submitted, close_order)
                    if D(close_order.filled_quantity) > 0:
                        exits.extend(facts.alpaca_fills(broker, close_order))
            if len({fill.fill_id for fill in exits}) != len(exits):
                raise BrokerError("The broker exit activity contains duplicate executions.")
            closed = sum((D(fill.quantity) for fill in exits), D(0))
            remaining = entered - closed
            closing_notional = sum((D(fill.quantity) * D(fill.price) for fill in exits), D(0))
    if remaining < 0:
        raise BrokerError(
            "Broker exits exceed the attributed entry; inspect protective fill races and external orders."
        )
    holdings = [
        item
        for item in broker.positions()
        if item.symbol == order.symbol and item.side == ("long" if order.side == "buy" else "short")
    ]
    if len(holdings) > 1:
        raise BrokerError("The broker returned ambiguous position holdings.")
    holding = D(holdings[0].quantity) if holdings else D(0)
    if not holding.is_finite() or holding < 0:
        raise BrokerError("The broker holding quantity is invalid.")
    previous = latest(db, entry)
    if previous and (entered < D(previous.entry_quantity) or closed < D(previous.closed_quantity)):
        raise BrokerError(
            "Cumulative broker position evidence moved backwards; reconciliation is required."
        )
    status = (
        "unfilled"
        if entered == 0
        else "closed"
        if remaining == 0
        and order.status in execution.TERMINAL
        and all(protection.status in execution.TERMINAL for protection in protections)
        else "unknown"
        if remaining == 0
        else "partially_closed"
        if closed > 0
        else "open"
    )
    gross = None
    if entry.provider == "alpaca" and entered:
        gross = (closing_notional - closed * entry_notional / entered) * (
            1 if order.side == "buy" else -1
        )
    return BrokerLifecycleView(
        entry_intent_id=entry.id,
        provider=entry.provider,
        symbol=entry.payload["order"]["symbol"],
        side="long" if entry.payload["order"]["side"] == "buy" else "short",
        currency=account.currency,
        notional_currency=entry.payload["order"]["symbol"][-3:]
        if entry.provider == "oanda"
        else "USD",
        status=status,
        entry_quantity=format(entered, "f"),
        closed_quantity=format(closed, "f"),
        remaining_quantity=format(remaining, "f"),
        broker_holding_quantity=format(holding, "f"),
        holding_check="closed"
        if remaining == 0
        else "covered"
        if holding >= remaining
        else "shortfall",
        entry_notional=format(entry_notional, "f"),
        closing_notional=format(closing_notional, "f"),
        realized_gross_pnl=format(gross, "f") if gross is not None else None,
        broker_realized_pnl_home=broker_pnl,
        broker_financing_home=financing,
        protections=protections,
        entry_fills=entry_fills,
        exit_fills=exits,
        trade_ids=trade_ids,
        protection_status=(
            "not_required"
            if not remaining
            else "covered"
            if sum(
                (
                    D(p.remaining_quantity or "0")
                    for p in protections
                    if p.kind in {"stop_loss", "trailing_stop"}
                    and p.status not in execution.TERMINAL
                    and p.status not in {"unknown", "inactive"}
                ),
                D(0),
            )
            == remaining
            else "incomplete"
            if any(p.status == "unknown" for p in protections)
            else "unprotected"
        ),
        observed_at=now(),
        detail=(
            "Zero attributed quantity; waiting for entry/protection terminal states. "
            if status == "unknown"
            else ""
        )
        + (
            "Broker trade P&L and financing are separate home-currency amounts."
            if entry.provider == "oanda"
            else "Virtual attribution from actual execution activities; gross P&L excludes fees, dividends and tax-lot differences."
        ),
    )


def verify_close_order(entry, request, submitted, order):
    if (
        order.client_order_id != request.payload["client_order_id"]
        or order.symbol != entry.payload["order"]["symbol"]
        or (order.side == "buy") == (entry.payload["order"]["side"] == "buy")
        or order.requested_quantity is None
        or D(order.requested_quantity) != D(submitted["quantity"])
    ):
        raise BrokerError("The broker close order differs from its durable request.")


def save_position(db, owner, entry_id, position):
    entry = execution.owned(db, owner, entry_id, lock=True)
    cursor = ensure_cursor(db, entry)
    previous = latest(db, entry)
    if previous and (
        D(position.entry_quantity) < D(previous.entry_quantity)
        or D(position.closed_quantity) < D(previous.closed_quantity)
    ):
        raise BrokerError("Broker position evidence decreased.")
    if cursor.status == "closed" and position.status != "closed":
        raise BrokerError("A closed broker position changed; reconciliation is required.")
    current = position.model_dump(mode="json")
    if previous:
        before, after = previous.model_dump(mode="json"), dict(current)
        before.pop("observed_at", None)
        after.pop("observed_at", None)
        if before == after:
            cursor.status = position.status
            cursor.updated_at = now()
            db.commit()
            return position
    append(db, entry, "position_observed", {"detail": position.detail, "position": current})
    cursor.status = position.status
    cursor.updated_at = now()
    db.commit()
    return position


def refresh(db, owner, entry_id):
    entry = execution.owned(db, owner, entry_id)
    if entry.payload["order"]["position_effect"] != "open":
        raise HTTPException(409, "Only entry orders have attributed position lifecycles.")
    try:
        with execution.matching_broker(db, entry) as broker:
            with localcontext() as context:
                context.prec = 120
                position = collect(db, entry, broker)
        return save_position(db, owner, entry_id, position)
    except BrokerError as error:
        db.rollback()
        entry = execution.owned(db, owner, entry_id, lock=True)
        cursor = ensure_cursor(db, entry)
        if cursor.status != "closed":
            cursor.status = "unknown"
        cursor.updated_at = now()
        append(db, entry, "position_unavailable", {"detail": str(error)})
        db.commit()
        raise HTTPException(409, str(error)) from None


def request_close(db, owner, entry_id, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    entry = execution.owned(db, owner, entry_id, lock=True)
    existing = db.scalar(
        select(BrokerCloseRequest).where(
            BrokerCloseRequest.owner_id == owner.id,
            BrokerCloseRequest.request_id == str(body.request_id),
        )
    )
    request = body.model_dump(mode="json")
    if existing:
        if existing.entry_intent_id != entry_id or existing.payload["request"] != request:
            raise HTTPException(409, "This close request ID was already used for another action.")
        return close_view(db, existing)
    if entry.payload["order"]["position_effect"] != "open":
        raise HTTPException(409, "A close must reference an attributed entry.")
    unresolved = db.scalar(
        select(BrokerCloseRequest).where(
            BrokerCloseRequest.entry_intent_id == entry_id,
            BrokerCloseRequest.status.not_in(["filled", "rejected"]),
        )
    )
    if unresolved:
        raise HTTPException(
            409, "Resolve the existing close request before creating another close."
        )
    with execution.matching_broker(db, entry) as broker:
        try:
            position = collect(db, entry, broker)
        except BrokerError as error:
            raise HTTPException(409, str(error)) from None
    if D(body.quantity) > D(position.remaining_quantity):
        raise HTTPException(409, "The close quantity exceeds the remaining attributed position.")
    if position.holding_check == "shortfall":
        raise HTTPException(
            409,
            "Broker holdings do not cover this attributed position; reconcile external trades before closing.",
        )
    close_id = identifier()
    payload = {
        "request": request,
        "client_order_id": "tauric_close_" + close_id.replace("-", ""),
        "initial_closed_quantity": position.closed_quantity,
        "initial_remaining_quantity": position.remaining_quantity,
        "trade_ids": position.trade_ids,
        "account_fingerprint": entry.account_fingerprint,
        "protection": {
            kind: entry.payload["order"].get(kind)
            or next((p.price for p in position.protections if p.kind == kind and p.price), None)
            for kind in ("stop_loss", "take_profit")
        },
    }
    row = BrokerCloseRequest(
        id=close_id,
        owner_id=owner.id,
        entry_intent_id=entry.id,
        request_id=str(body.request_id),
        payload=payload,
        checksum=execution.checksum(payload),
        status="prepared",
    )
    db.add(row)
    db.flush()
    cursor = ensure_cursor(db, entry)
    cursor.status, cursor.updated_at = position.status, now()
    append(
        db,
        entry,
        "position_observed",
        {"detail": position.detail, "position": position.model_dump(mode="json")},
    )
    append(
        db,
        entry,
        "close_prepared",
        {"close_request_id": row.id, "detail": body.reason, "quantity": body.quantity},
    )
    db.commit()
    return process_close(db, owner, row.id)


def owned_close(db, owner, close_id):
    row = db.scalar(
        select(BrokerCloseRequest)
        .where(BrokerCloseRequest.id == close_id, BrokerCloseRequest.owner_id == owner.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not row:
        raise HTTPException(404, "This broker close request does not exist.")
    if row.checksum != execution.checksum(row.payload):
        raise HTTPException(409, "Broker close authority integrity failed.")
    return row


def note_close(db, owner, row, status, detail, kind="close_status", **extra):
    row = owned_close(db, owner, row.id)
    entry = execution.owned(db, owner, row.entry_intent_id, lock=True)
    if row.status not in ("filled", "rejected"):
        row.status = status
    row.updated_at = now()
    append(db, entry, kind, {"close_request_id": row.id, "detail": detail, **extra})
    db.commit()
    return close_view(db, row)


def finish_close(db, owner, row, entry, broker, position, status, detail):
    """Completion includes verified replacement protection for a surviving remainder."""
    if entry.provider == "alpaca" and D(position.remaining_quantity) > 0:
        levels = row.payload.get("protection", {})
        if any(levels.values()):
            existing = next(
                reversed(
                    [
                        s
                        for s in replacement_submissions(db, entry)
                        if s.get("close_request_id") == row.id
                    ]
                ),
                None,
            )
            if existing is None:
                if position.holding_check != "covered":
                    raise BrokerError(
                        "Cannot restore protection while attributed holdings are unresolved."
                    )
                active = [p for p in position.protections if p.status not in execution.TERMINAL]
                if active:
                    # A protective fill may satisfy the requested partial close.
                    # Preserve surviving broker coverage without adding another sell.
                    if position.protection_status == "covered":
                        return note_close(db, owner, row, status, detail)
                    raise BrokerError("Existing protection must be reconciled before replacement.")
                existing = {
                    "close_request_id": row.id,
                    "client_order_id": "tauric_protect_" + row.id.replace("-", ""),
                    "symbol": position.symbol,
                    "side": "sell" if position.side == "long" else "buy",
                    "quantity": position.remaining_quantity,
                    **levels,
                }
                note_close(
                    db,
                    owner,
                    row,
                    "unknown",
                    "Restoring broker-held protection to the attributed remainder.",
                    "protection_submission_started",
                    **{k: v for k, v in existing.items() if k != "close_request_id"},
                )
                facts.alpaca_protect(broker, existing)
            else:
                # A lost submission response never grants permission to repeat.
                facts.alpaca_replacement(broker, existing)
            updated = collect(db, entry, broker)
            save_position(db, owner, entry.id, updated)
            replacements = facts.alpaca_replacement(broker, existing)
            expected = {kind for kind, value in levels.items() if value}
            active = {
                p.kind
                for _, p in replacements
                if p.status not in execution.TERMINAL
                and p.status not in {"unknown", "inactive"}
                and D(p.remaining_quantity or "0") == D(updated.remaining_quantity)
            }
            if D(updated.remaining_quantity) and not expected.issubset(active):
                raise BrokerError(
                    "Replacement protection is unresolved or rejected; entry remains an operating incident."
                )
            detail += " Remainder protection verified at the broker."
    return note_close(db, owner, row, status, detail)


def process_close(db, owner, close_id):
    # A separate PostgreSQL transaction holds this lock across every durable
    # commit and network call. Worker/API races never issue the same mutation.
    row = db.scalar(
        select(BrokerCloseRequest).where(
            BrokerCloseRequest.id == close_id, BrokerCloseRequest.owner_id == owner.id
        )
    )
    if not row:
        raise HTTPException(404, "This broker close request does not exist.")
    key = int.from_bytes(
        blake2b(("broker-position:" + row.entry_intent_id).encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )
    with db.get_bind().connect() as guard:
        acquired = guard.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
        if not acquired:
            db.rollback()
            return close_view(db, db.get(BrokerCloseRequest, close_id))
        with localcontext() as context:
            context.prec = 120
            return _process_close(db, owner, close_id)


def _process_close(db, owner, close_id):
    row = owned_close(db, owner, close_id)
    if row.status in ("filled", "rejected"):
        return close_view(db, row)
    entry = execution.owned(db, owner, row.entry_intent_id, lock=True)
    try:
        with execution.matching_broker(db, entry) as broker:
            if broker.account().account_fingerprint != entry.account_fingerprint:
                raise BrokerError("The close request account differs from the original entry.")
            order = broker.order(entry.client_order_id)
            if order.status not in execution.TERMINAL:
                db.commit()
                execution.cancel(db, owner, entry.id)
                return note_close(
                    db,
                    owner,
                    row,
                    row.status,
                    "Waiting for the entry remainder to stop filling before closing.",
                )
            position = collect(db, entry, broker)
            target = D(row.payload["initial_closed_quantity"]) + D(
                row.payload["request"]["quantity"]
            )
            if D(position.closed_quantity) >= target:
                save_position(db, owner, entry.id, position)
                return finish_close(
                    db,
                    owner,
                    row,
                    entry,
                    broker,
                    position,
                    "filled",
                    "Actual broker exits fulfilled this close request.",
                )
            submitted = close_submission(db, entry, row)
            if submitted:
                save_position(db, owner, entry.id, position)
                if close_not_submitted(db, entry, row):
                    return finish_close(
                        db,
                        owner,
                        row,
                        entry,
                        broker,
                        position,
                        "rejected",
                        "Close was not submitted; surviving protection restored.",
                    )
                status = "pending" if row.status != "unknown" else "unknown"
                if entry.provider == "alpaca":
                    closing = broker.order(row.payload["client_order_id"])
                    verify_close_order(entry, row, submitted, closing)
                    if closing.status in ("cancelled", "rejected", "expired"):
                        return finish_close(
                            db,
                            owner,
                            row,
                            entry,
                            broker,
                            position,
                            "rejected",
                            "The broker close order ended before completing the requested exit.",
                        )
                    status = "unknown" if closing.status == "unknown" else "pending"
                return note_close(
                    db,
                    owner,
                    row,
                    status,
                    "Close outcome remains unresolved; no submission is repeated.",
                )
            if entry.provider == "alpaca":
                children = alpaca_children(db, entry, broker, order)
                issued = {
                    event.payload.get("broker_order_id")
                    for event in execution.events(db, entry)
                    if event.kind == "protection_cancel_started"
                    and event.payload.get("close_request_id") == row.id
                }
                for child, _ in children:
                    if (
                        child.status not in execution.TERMINAL
                        and child.broker_order_id not in issued
                    ):
                        note_close(
                            db,
                            owner,
                            row,
                            "cancelling_protection",
                            "Cancelling broker-held protection before a manual close.",
                            "protection_cancel_started",
                            broker_order_id=child.broker_order_id,
                        )
                        broker.request(
                            "DELETE", "/v2/orders/" + facts.identifier(child.broker_order_id)
                        )
                children = alpaca_children(db, entry, broker, order)
                if any(child.status not in execution.TERMINAL for child, _ in children):
                    return note_close(
                        db,
                        owner,
                        row,
                        "cancelling_protection",
                        "Waiting for protective cancellations; fills can still occur.",
                    )
                position = collect(db, entry, broker)
            quantity = target - D(position.closed_quantity)
            if quantity <= 0:
                save_position(db, owner, entry.id, position)
                return finish_close(
                    db,
                    owner,
                    row,
                    entry,
                    broker,
                    position,
                    "filled",
                    "Broker protection completed the exit before a manual close was submitted.",
                )
            if quantity > D(position.remaining_quantity):
                raise BrokerError(
                    "The requested attributed close no longer fits the broker position."
                )
            if position.holding_check == "shortfall":
                raise BrokerError(
                    "Broker holdings no longer cover the attributed position; no close was submitted."
                )
            if entry.provider == "oanda" and len(position.trade_ids) != 1:
                raise BrokerError("A targeted OANDA close requires one unambiguous entry trade.")
            quantity_text = format(quantity, "f")
            note_close(
                db,
                owner,
                row,
                "submitting",
                "Close mutation may have been submitted; recovery only reconciles.",
                "close_submission_started",
                quantity=quantity_text,
            )
            try:
                if entry.provider == "oanda":
                    facts.oanda_close_trade(broker, position.trade_ids[0], quantity_text)
                else:
                    broker.submit(
                        BrokerOrderRequest(
                            client_order_id=row.payload["client_order_id"],
                            symbol=position.symbol,
                            side="sell" if position.side == "long" else "buy",
                            quantity=quantity_text,
                            order_type="market",
                            time_in_force="day",
                            position_effect="reduce",
                        )
                    )
            except BrokerError as error:
                if error.not_submitted:
                    note_close(db, owner, row, "unknown", str(error), "close_not_submitted")
                raise
            updated = collect(db, entry, broker)
            save_position(db, owner, entry.id, updated)
            if D(updated.closed_quantity) >= target:
                return finish_close(
                    db,
                    owner,
                    row,
                    entry,
                    broker,
                    updated,
                    "filled",
                    "Broker close filled; actual executions recorded.",
                )
            return note_close(
                db,
                owner,
                row,
                "pending",
                "Broker close submitted; actual executions determine completion.",
            )
    except BrokerError as error:
        db.rollback()
        return note_close(db, owner, row, "unknown", str(error))


def repair_protection(db, owner, close_id, body):
    """A new attempt is allowed only after every previous replacement is terminal."""
    row = owned_close(db, owner, close_id)
    entry = execution.owned(db, owner, row.entry_intent_id, lock=True)
    key = int.from_bytes(
        blake2b(("broker-position:" + entry.id).encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )
    with db.get_bind().connect() as guard:
        if not guard.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
            raise HTTPException(409, "This position is being reconciled. Try again shortly.")
        with execution.matching_broker(db, entry) as broker:
            try:
                if entry.provider != "alpaca" or row.status in ("filled", "rejected"):
                    raise HTTPException(
                        409, "Only an unresolved Alpaca close can repair protection."
                    )
                attempts = [
                    s
                    for s in replacement_submissions(db, entry)
                    if s.get("close_request_id") == row.id
                ]
                repeated = next(
                    (s for s in attempts if s.get("repair_request_id") == str(body.request_id)),
                    None,
                )
                if repeated:
                    if repeated.get("repair_request") != body.model_dump(mode="json"):
                        raise HTTPException(409, "This repair request ID has different inputs.")
                    # Reconcile the same attempt; never repeat a lost response.
                    return _process_close(db, owner, close_id)
                if not attempts:
                    raise HTTPException(
                        409, "Reconcile the close before requesting protection repair."
                    )
                for attempt in attempts:
                    if any(
                        o.status not in execution.TERMINAL
                        for o, _ in facts.alpaca_replacement(broker, attempt)
                    ):
                        raise HTTPException(
                            409, "Previous protection is active or uncertain; reconcile it first."
                        )
                position = collect(db, entry, broker)
                order = broker.order(entry.client_order_id)
                children = alpaca_children(db, entry, broker, order)
                if (
                    position.holding_check != "covered"
                    or not D(position.remaining_quantity)
                    or any(o.status not in execution.TERMINAL for o, _ in children)
                ):
                    raise HTTPException(
                        409, "Repair requires a verified remainder and no active protection."
                    )
                submitted = close_submission(db, entry, row)
                if submitted and not close_not_submitted(db, entry, row):
                    closing = broker.order(row.payload["client_order_id"])
                    verify_close_order(entry, row, submitted, closing)
                    if closing.status not in execution.TERMINAL:
                        raise HTTPException(409, "The close order is still active or uncertain.")
                replacement = {
                    "client_order_id": "tauric_repair_" + body.request_id.hex,
                    "symbol": position.symbol,
                    "side": "sell" if position.side == "long" else "buy",
                    "quantity": position.remaining_quantity,
                    "repair_request_id": str(body.request_id),
                    "repair_request": body.model_dump(mode="json"),
                    **row.payload["protection"],
                }
                note_close(
                    db,
                    owner,
                    row,
                    "unknown",
                    body.reason,
                    "protection_submission_started",
                    **replacement,
                )
                facts.alpaca_protect(broker, replacement)
                return _process_close(db, owner, close_id)
            except BrokerError as error:
                db.rollback()
                return note_close(db, owner, row, "unknown", str(error))


def journal(db, owner):
    entries = db.scalars(
        select(BrokerOrderIntent)
        .where(BrokerOrderIntent.owner_id == owner.id)
        .order_by(BrokerOrderIntent.created_at)
    ).all()
    positions = [position for entry in entries if (position := latest(db, entry)) is not None]
    closes = db.scalars(
        select(BrokerCloseRequest)
        .where(BrokerCloseRequest.owner_id == owner.id)
        .order_by(BrokerCloseRequest.created_at.desc())
    ).all()
    return BrokerJournalView(
        positions=positions,
        close_requests=[close_view(db, row) for row in closes],
        detail="Actual broker executions and virtual entry attribution. External holdings are shown by account checks; fees and tax-lot outcomes are not inferred.",
    )


def process_one(db):
    request = db.scalar(
        select(BrokerCloseRequest)
        .where(
            BrokerCloseRequest.status.not_in(["filled", "rejected"]),
            BrokerCloseRequest.updated_at < now() - timedelta(seconds=15),
        )
        .order_by(BrokerCloseRequest.updated_at)
        .limit(1)
    )
    if request:
        process_close(db, db.get(Owner, request.owner_id), request.id)
        return True
    cursor = db.scalar(
        select(BrokerPositionCursor)
        .where(
            BrokerPositionCursor.status.not_in(["closed", "unfilled"]),
            BrokerPositionCursor.updated_at < now() - timedelta(seconds=15),
        )
        .order_by(BrokerPositionCursor.updated_at)
        .limit(1)
    )
    if cursor:
        try:
            refresh(db, db.get(Owner, cursor.owner_id), cursor.entry_intent_id)
        except HTTPException:
            db.rollback()
        return True
    return False
