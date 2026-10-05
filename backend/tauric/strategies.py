"""Versioned strategy configuration and explicit paper account/allocation routes."""

from sqlalchemy import select

from . import paper, services
from .models import DeploymentControlVersion, Owner, StrategyVersion, identifier
from .strategy_contracts import DeploymentControlView, StrategyVersionView


def version(db, owner, version_id):
    row = db.get(StrategyVersion, str(version_id))
    if not row or row.owner_id != owner.id:
        paper.fail("This strategy version does not exist.", 404)
    if paper.checksum(row.payload) != row.checksum:
        paper.fail("Strategy version integrity failed.")
    return row


def view(row):
    if paper.checksum(row.payload) != row.checksum:
        paper.fail("Strategy version integrity failed.")
    return StrategyVersionView(
        id=row.id,
        strategy_id=row.strategy_id,
        name=row.name,
        revision=row.revision,
        parameters=row.payload["parameters"],
        reason=row.payload["reason"],
        checksum=row.checksum,
        created_at=row.created_at,
    )


def versions(db, owner, strategy_id=None):
    query = select(StrategyVersion).where(StrategyVersion.owner_id == owner.id)
    if strategy_id:
        first = version(db, owner, strategy_id)
        if first.revision != 1:
            paper.fail("Use the strategy identity to read its history.", 404)
        query = query.where(StrategyVersion.strategy_id == str(strategy_id))
    return [
        view(row)
        for row in db.scalars(
            query.order_by(StrategyVersion.created_at.desc(), StrategyVersion.revision.desc())
        )
    ]


def save(db, owner, body, strategy_id=None):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    request = {
        **body.model_dump(mode="json", exclude_none=True),
        "strategy_id": str(strategy_id) if strategy_id else None,
    }
    previous = db.scalar(
        select(StrategyVersion).where(
            StrategyVersion.owner_id == owner.id, StrategyVersion.request_id == str(body.request_id)
        )
    )
    if previous:
        if previous.payload["request"] != request:
            paper.fail("This request identifier was already used for different strategy inputs.")
        return view(previous)
    head = None
    if strategy_id:
        initial = version(db, owner, strategy_id)
        if initial.revision != 1:
            paper.fail("Use the strategy identity when creating a version.", 422)
        head = db.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == str(strategy_id))
            .order_by(StrategyVersion.revision.desc())
            .limit(1)
        )
        if head.revision != body.expected_revision:
            paper.fail("This strategy changed in another window. Reload before creating a version.")
    new_id = identifier()
    row = StrategyVersion(
        id=new_id,
        strategy_id=head.strategy_id if head else new_id,
        owner_id=owner.id,
        name=head.name if head else body.name.strip(),
        revision=head.revision + 1 if head else 1,
        request_id=str(body.request_id),
        payload={
            "parameters": body.parameters.model_dump(exclude_none=True),
            "reason": body.reason,
            "request": request,
        },
    )
    row.checksum = paper.checksum(row.payload)
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "strategy.version_created",
        "strategy_version",
        row.id,
        body.reason,
        {"strategy_id": row.strategy_id, "revision": row.revision},
    )
    db.commit()
    return view(row)


def control(db, dep):
    row = db.scalar(
        select(DeploymentControlVersion)
        .where(DeploymentControlVersion.deployment_id == dep.id)
        .order_by(DeploymentControlVersion.revision.desc())
        .limit(1)
    )
    if row:
        if row.owner_id != dep.owner_id or paper.checksum(row.payload) != row.checksum:
            paper.fail("Deployment control integrity failed. New entries are blocked.")
        return DeploymentControlView(
            revision=row.revision,
            enabled=row.payload["enabled"],
            reason=row.payload["reason"],
            created_at=row.created_at,
        )
    return DeploymentControlView(
        revision=0, enabled=True, reason="Initial immutable paper deployment", created_at=None
    )


def require_enabled(db, dep):
    if dep.payload.get("strategy_revision_id"):
        row = db.get(StrategyVersion, dep.payload["strategy_revision_id"])
        if not row or row.owner_id != dep.owner_id or paper.checksum(row.payload) != row.checksum:
            paper.fail("The pinned strategy version cannot be verified.")
    if not control(db, dep).enabled:
        paper.fail(
            "This strategy deployment is paused. Existing attributed positions can still be closed."
        )


def change_control(db, owner, deployment_id, body, *, commit=True):
    dep = paper.deployment(db, owner, deployment_id)
    acct = paper.account(db, owner, dep.account_id)
    current = control(db, dep)
    if current.revision != body.expected_revision:
        paper.fail("This deployment changed in another window. Reload before saving.")
    payload = {
        "enabled": body.enabled,
        "reason": body.reason,
        "request": body.model_dump(mode="json"),
    }
    row = DeploymentControlVersion(
        owner_id=owner.id,
        deployment_id=dep.id,
        revision=current.revision + 1,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    acct.revision += 1
    db.flush()
    services.audit(
        db,
        owner,
        "paper.deployment_control",
        "paper_deployment",
        dep.id,
        body.reason,
        {"revision": row.revision, "enabled": body.enabled, "version_id": row.id},
    )
    if commit:
        db.commit()
    return control(db, dep)


def control_history(db, owner, deployment_id):
    paper.deployment(db, owner, deployment_id)
    rows = db.scalars(
        select(DeploymentControlVersion)
        .where(DeploymentControlVersion.deployment_id == str(deployment_id))
        .order_by(DeploymentControlVersion.revision.desc())
    )
    result = []
    for row in rows:
        if paper.checksum(row.payload) != row.checksum:
            paper.fail("Deployment history integrity failed.")
        result.append(
            {
                "id": row.id,
                "revision": row.revision,
                "payload": row.payload,
                "checksum": row.checksum,
                "created_at": row.created_at.isoformat(),
            }
        )
    return result


def validate_proposal(db, dep, prop):
    require_enabled(db, dep)
    if prop.payload.get("strategy_control_revision", 0) != control(db, dep).revision:
        paper.fail("Deployment controls changed. Create a new proposal.")
    from . import strategy_execution

    strategy_execution.validate_proposal(db, dep, prop)
