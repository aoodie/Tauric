"""Separate trusted source transition for an installation with no financial authority.

This is not strategy qualification. The normal autonomous release path continues
to require an unchanged execution signature. An operator must supply independent
checked-release evidence, and all authority tables must be empty before this
separate research-only path can accept changed execution code.
"""

import re

FINANCIAL_SERVICES = {"broker_execution", "valuation", "forward_validation", "autonomous_paper"}
EMPTY_TABLES = {
    "broker_policy_versions", "broker_routes", "broker_route_versions",
    "broker_order_intents", "broker_order_events", "broker_position_cursors",
    "broker_close_requests", "broker_review_jobs", "broker_review_decisions",
    "broker_route_workflows", "broker_route_events", "qualification_protocols",
    "qualification_evaluations", "qualification_admissions", "qualification_reports",
    "trading_readiness_records", "autonomy_workflows", "autonomy_events",
}


def verify(proof, *, revision, before_signature, after_signature, snapshot, services):
    if (
        not isinstance(proof, dict)
        or set(proof) != {"kind", "revision", "before_signature", "after_signature", "release_run_id", "checked_release_verified"}
        or proof["kind"] != "trusted_research_only"
        or proof["revision"] != revision
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
        or proof["before_signature"] != before_signature
        or proof["after_signature"] != after_signature
        or type(proof["release_run_id"]) is not int or proof["release_run_id"] <= 0
        or proof["checked_release_verified"] is not True
        or not services or "db" not in services or FINANCIAL_SERVICES & set(services)
    ):
        raise ValueError("Trusted research-only source transition evidence is missing or mismatched.")
    tables = snapshot.get("tables", {})
    if any(name not in tables or tables[name].get("count") != 0 for name in EMPTY_TABLES):
        raise ValueError("Research-only source transition refuses existing financial authority or evidence.")
    return after_signature
