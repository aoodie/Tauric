"""Broker management through owner services, preserving their execution checks."""

from uuid import UUID

from fastapi import HTTPException

from . import broker_execution, broker_positions, broker_review, broker_routes, broker_setup
from .broker_contracts import (
    BrokerCloseAction,
    BrokerPolicyChange,
    BrokerProvider,
    BrokerReviewedEntrySubmit,
    BrokerReviewRequest,
    BrokerRouteChange,
    BrokerSetupRequest,
)
from .live_contracts import ForwardRegister, ReadinessAction


class PolicyChange(BrokerPolicyChange):
    provider: BrokerProvider


class RouteChange(BrokerRouteChange):
    route_id: UUID


class ReviewedEntry(BrokerReviewedEntrySubmit):
    job_id: UUID


class ClosePosition(BrokerCloseAction):
    intent_id: UUID


class OrderAction(BrokerReviewedEntrySubmit):
    intent_id: UUID


class CloseAction(BrokerReviewedEntrySubmit):
    close_id: UUID


class ForwardEvaluation(ReadinessAction):
    protocol_id: UUID


class AccountCheck(BrokerReviewedEntrySubmit):
    provider: BrokerProvider


CONTRACTS = {
    "capture_broker_valuation": ReadinessAction,
    "register_broker_forward": ForwardRegister,
    "evaluate_broker_forward": ForwardEvaluation,
    "check_broker_account": AccountCheck,
    "save_broker_policy": PolicyChange,
    "save_broker_route": RouteChange,
    "assess_broker_setup": BrokerSetupRequest,
    "request_broker_review": BrokerReviewRequest,
    "submit_broker_entry": ReviewedEntry,
    "close_broker_position": ClosePosition,
    "reconcile_broker_order": OrderAction,
    "cancel_broker_order": OrderAction,
    "refresh_broker_position": OrderAction,
    "reconcile_broker_close": CloseAction,
    "repair_broker_protection": CloseAction,
}
TITLES = {name: name.replace("_", " ").capitalize() for name in CONTRACTS}
# Calls that can reach broker writes. Preparation/policy/route operations have
# separate services and never imply that a broker fill occurred.
EXECUTION_OPERATIONS = {
    "request_broker_review",
    "submit_broker_entry",
    "close_broker_position",
    "cancel_broker_order",
    "reconcile_broker_close",
    "repair_broker_protection",
}
READS = {
    "broker_readiness",
    "broker_state",
    "broker_routes",
    "broker_journal",
    "broker_reviews",
    "broker_setups",
}


def read(db, owner, resource, resource_id=None):
    if resource == "broker_readiness":
        from . import live_trading

        return live_trading.state(db, owner).model_dump(mode="json")
    if resource == "broker_state":
        return broker_execution.state(db, owner).model_dump(mode="json")
    if resource == "broker_routes":
        return broker_routes.state(db, owner).model_dump(mode="json")
    if resource == "broker_journal":
        return broker_positions.journal(db, owner).model_dump(mode="json")
    if resource == "broker_reviews":
        values = broker_review.views(db, owner)
    else:
        values = (
            [broker_setup.report(db, owner, str(resource_id))]
            if resource_id
            else broker_setup.reports(db, owner)
        )
    return {"records": [value.model_dump(mode="json") for value in values]}


def execute(db, owner, operation, params):
    def body(contract, *excluded):
        return contract(**params.model_dump(exclude=set(excluded)))

    if operation in {
        "capture_broker_valuation",
        "register_broker_forward",
        "evaluate_broker_forward",
    }:
        from . import live_trading, valuation

        if operation == "capture_broker_valuation":
            return live_trading.record_view(valuation.capture(db, owner, params))
        if operation == "register_broker_forward":
            return live_trading.register(db, owner, params)
        return live_trading.evaluate(
            db, owner, params.protocol_id, body(ReadinessAction, "protocol_id")
        )
    if operation == "check_broker_account":
        return broker_execution.check_account(db, params.provider)
    if operation == "save_broker_policy":
        return broker_execution.save_policy(
            db, owner, params.provider, body(BrokerPolicyChange, "provider")
        )
    if operation == "save_broker_route":
        return broker_routes.save(db, owner, params.route_id, body(BrokerRouteChange, "route_id"))
    if operation == "assess_broker_setup":
        return broker_setup.enqueue(db, owner, params)
    if operation == "request_broker_review":
        return broker_review.enqueue(db, owner, params)
    if operation == "submit_broker_entry":
        job = broker_review.checked(db, owner, str(params.job_id))
        if not job.decision_id:
            raise HTTPException(409, "This broker review has no finalized entry permission.")
        return broker_review.prepare_entry(
            db, owner, job.decision_id, body(BrokerReviewedEntrySubmit, "job_id")
        )
    if operation == "close_broker_position":
        return broker_positions.request_close(
            db, owner, str(params.intent_id), body(BrokerCloseAction, "intent_id")
        )
    if operation == "reconcile_broker_order":
        return broker_execution.reconcile(db, owner, str(params.intent_id))
    if operation == "cancel_broker_order":
        return broker_execution.cancel(db, owner, str(params.intent_id))
    if operation == "refresh_broker_position":
        return broker_positions.refresh(db, owner, str(params.intent_id))
    if operation == "reconcile_broker_close":
        return broker_positions.process_close(db, owner, str(params.close_id))
    if operation == "repair_broker_protection":
        return broker_positions.repair_protection(
            db, owner, str(params.close_id), body(BrokerReviewedEntrySubmit, "close_id")
        )
    raise ValueError("Unknown broker management operation")
