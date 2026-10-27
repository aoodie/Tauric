"""Completion checks requested evidence and owner-authorized dependency removal."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_agent_tasks import invocation

from tauric import agent_tasks
from tauric.agent_contracts import TaskControl, TaskCreate
from tauric.agent_models import AgentTask
from tauric.models import AuditEvent, Owner, now

URL = "https://example.org/eur-usd"


def task(db, owner, **extra):
    return agent_tasks.create(db, owner, TaskCreate(
        request_id=uuid4(), objective="Read the specified EUR/USD source",
        criteria=[{"description": "Fresh original source at the specified URL",
                   "proof": "source_evidence", "binding": {"url": URL}}], **extra,
    ))


def source(db, owner, url=URL, age=0):
    evidence = {"url": url, "text": "Bound offline source fixture"}
    row = AuditEvent(owner_id=owner.id, actor="owner", action="chat.open_research_observed",
        resource_type="source", resource_id=str(uuid4()), reason="Offline evidence fixture",
        details={"operation": "read", "evidence": evidence,
                 "checksum": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()},
        created_at=now() - timedelta(days=age))
    db.add(row)
    db.commit()
    return row.id


def complete(db, owner, goal, evidence):
    return agent_tasks.invoke(db, owner, None, invocation(goal["id"], "complete", {
        "evidence": [{"kind": "open_research", "id": evidence}],
        "summary": "The requested original evidence was checked",
    }))


@pytest.mark.parametrize("url,age,accepted", [
    (URL, 0, True), ("https://example.org/unrelated", 0, False),
    (URL, 7, False), (URL, -1, False),
])
def test_wrong_stale_or_future_sources_cannot_complete_goal(database, url, age, accepted):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        goal = task(db, owner)
        evidence = source(db, owner, url, age)
        if accepted:
            assert complete(db, owner, goal, evidence)["status"] == "completed"
        else:
            with pytest.raises(HTTPException, match="criterion"):
                complete(db, owner, goal, evidence)
            assert db.get(AgentTask, goal["id"]).status == "queued"


def test_legacy_unbound_description_cannot_claim_verified_completion(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        goal = agent_tasks.create(db, owner, TaskCreate(request_id=uuid4(),
            objective="An unstructured legacy goal", criteria=[{
                "description": "Find fresh relevant evidence", "proof": "source_evidence"}]))
        with pytest.raises(HTTPException, match="bindings"):
            complete(db, owner, goal, source(db, owner))


def test_cancelled_child_only_stops_blocking_after_explicit_owner_scope_removal(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        parent = task(db, owner)
        child = task(db, owner, parent_id=parent["id"])
        evidence = source(db, owner)
        agent_tasks.control(db, owner, child["id"], TaskControl(
            expected_revision=child["revision"], action="cancel", reason="Optional investigation no longer needed"))
        with pytest.raises(HTTPException, match="child tasks"):
            complete(db, owner, parent, evidence)
        agent_tasks.control(db, owner, parent["id"], TaskControl(
            expected_revision=db.get(AgentTask, parent["id"]).revision,
            action="waive_child", child_id=child["id"], reason="Explicit owner removal of optional scope"))
        result = complete(db, owner, parent, evidence)
        assert result["status"] == "completed"
        assert result["result"]["waived_children"][child["id"]]["owner_id"] == owner.id
        assert db.get(AgentTask, child["id"]).status == "cancelled"
