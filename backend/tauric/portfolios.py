"""Versioned mandates and server-bound chat around existing paper allocations."""

from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from . import capital, paper, services, strategies
from .models import (
    CapitalScopeVersion,
    ChatPortfolioBinding,
    ChatResearch,
    ChatTurn,
    Owner,
    PaperAccount,
    PaperDeployment,
    PortfolioActionReceipt,
    PortfolioPolicyVersion,
    identifier,
    now,
)
from .portfolio_contracts import (
    PortfolioContext,
    PortfolioPreferences,
    PortfolioReceipt,
    PortfolioView,
)
from .strategy_contracts import DeploymentControl


def checked(row):
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Portfolio history integrity failed.")
    return row


def roots(db, owner):
    result = {}
    for row in db.scalars(
        select(CapitalScopeVersion)
        .where(CapitalScopeVersion.owner_id == owner.id, CapitalScopeVersion.kind == "portfolio")
        .order_by(CapitalScopeVersion.revision)
    ):
        result[row.scope_id] = checked(row)
    return result


def heads(db, owner):
    result = {}
    for row in db.scalars(
        select(PortfolioPolicyVersion)
        .where(PortfolioPolicyVersion.owner_id == owner.id)
        .order_by(PortfolioPolicyVersion.revision)
    ):
        result[row.portfolio_id] = checked(row)
    return result


def resolve(db, owner, portfolio_id):
    portfolio_id = str(portfolio_id)
    root = roots(db, owner).get(portfolio_id)
    if not root:
        raise HTTPException(404, "Portfolio not found.")
    current = heads(db, owner)
    if any(
        key != portfolio_id and portfolio_id in row.payload["allocation_ids"]
        for key, row in current.items()
    ):
        raise HTTPException(409, "This allocation belongs to another portfolio mandate.")
    return root, current.get(portfolio_id)


def view(db, owner, portfolio_id):
    root, policy = resolve(db, owner, portfolio_id)
    ids = policy.payload["allocation_ids"] if policy else [root.scope_id]
    all_roots = roots(db, owner)
    accounts = []
    for scope_id in ids:
        allocation = all_roots.get(scope_id)
        if not allocation:
            raise HTTPException(409, "Portfolio account links cannot be verified.")
        acct = db.get(PaperAccount, allocation.account_id)
        if not acct or acct.owner_id != owner.id:
            raise HTTPException(409, "Portfolio account links cannot be verified.")
        accounts.append(
            {
                "id": acct.id,
                "name": acct.name,
                "currency": acct.currency,
                "allocation_id": scope_id,
                "mode": "paper",
            }
        )
    return PortfolioView(
        id=root.scope_id,
        name=root.name,
        revision=policy.revision if policy else 0,
        version_id=policy.id if policy else None,
        preferences=policy.payload["preferences"] if policy else None,
        allocation_ids=ids,
        accounts=accounts,
        capabilities={
            "scoped_chat": True,
            "preference_edits": True,
            "paper_deployment_control": True,
            "managed_reallocation": False,
            "automatic_research": True,
            "automatic_promotion": False,
            "live_execution": False,
        },
    )


def list_portfolios(db, owner):
    current = heads(db, owner)
    linked = {
        scope
        for key, row in current.items()
        for scope in row.payload["allocation_ids"]
        if scope != key
    }
    return [view(db, owner, key) for key in roots(db, owner) if key not in linked]


def save(db, owner, portfolio_id, body, *, commit=True, turn_id=None):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    root, previous = resolve(db, owner, portfolio_id)
    payload = body.model_dump(mode="json")
    old = db.scalar(
        select(PortfolioPolicyVersion).where(
            PortfolioPolicyVersion.owner_id == owner.id,
            PortfolioPolicyVersion.request_id == str(body.request_id),
        )
    )
    if old:
        if checked(old).portfolio_id != root.scope_id or old.payload != payload:
            raise HTTPException(
                409, "This request identifier was used for another portfolio change."
            )
        return view(db, owner, root.scope_id)
    if body.expected_revision != (previous.revision if previous else 0):
        raise HTTPException(409, "Portfolio settings changed. Reload before saving.")
    all_roots, current = roots(db, owner), heads(db, owner)
    ids = [str(key) for key in body.allocation_ids]
    if root.scope_id not in ids:
        raise HTTPException(422, "Keep the portfolio's original allocation linked.")
    for scope_id in ids:
        if scope_id not in all_roots:
            raise HTTPException(404, "Account allocation not found.")
        if scope_id != root.scope_id and (
            scope_id in current
            or db.scalar(
                select(ChatPortfolioBinding.conversation_id)
                .where(
                    ChatPortfolioBinding.owner_id == owner.id,
                    ChatPortfolioBinding.portfolio_id == scope_id,
                )
                .limit(1)
            )
        ):
            raise HTTPException(409, "This allocation already has its own mandate or chat.")
        if any(
            key != root.scope_id and scope_id in row.payload["allocation_ids"]
            for key, row in current.items()
        ):
            raise HTTPException(409, "This allocation is already linked to another portfolio.")
    row = PortfolioPolicyVersion(
        portfolio_id=root.scope_id,
        anchor_version_id=root.id,
        owner_id=owner.id,
        revision=(previous.revision if previous else 0) + 1,
        request_id=str(body.request_id),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "portfolio.preferences_changed",
        "capital_scope",
        root.scope_id,
        body.reason,
        {
            "revision": row.revision,
            "version_id": row.id,
            "turn_id": turn_id,
            "before": previous.payload if previous else None,
            "after": payload,
        },
    )
    if commit:
        db.commit()
    return view(db, owner, root.scope_id)


def history(db, owner, portfolio_id):
    resolve(db, owner, portfolio_id)
    return [
        {
            "id": row.id,
            "revision": row.revision,
            "payload": checked(row).payload,
            "checksum": row.checksum,
            "created_at": row.created_at.isoformat(),
        }
        for row in db.scalars(
            select(PortfolioPolicyVersion)
            .where(
                PortfolioPolicyVersion.owner_id == owner.id,
                PortfolioPolicyVersion.portfolio_id == str(portfolio_id),
            )
            .order_by(PortfolioPolicyVersion.revision.desc())
            .limit(50)
        )
    ]


def bind(db, owner, conversation_id, portfolio_id):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    conversation_id = str(conversation_id)
    portfolio_id = str(portfolio_id) if portfolio_id else None
    existing = db.get(ChatPortfolioBinding, conversation_id)
    if existing:
        if existing.owner_id != owner.id or existing.portfolio_id != portfolio_id:
            raise HTTPException(409, "This conversation is bound to a different context.")
        if portfolio_id:
            resolve(db, owner, portfolio_id)
        return
    legacy = db.scalar(select(ChatTurn).where(ChatTurn.conversation_id == conversation_id).limit(1))
    if legacy and (legacy.owner_id != owner.id or portfolio_id is not None):
        raise HTTPException(409, "Existing general conversations retain their original context.")
    anchor = resolve(db, owner, portfolio_id)[0].id if portfolio_id else None
    db.add(
        ChatPortfolioBinding(
            conversation_id=conversation_id,
            owner_id=owner.id,
            portfolio_id=portfolio_id,
            anchor_version_id=anchor,
        )
    )
    db.flush()


def deployment_ids(db, owner, portfolio):
    ids = set()
    for account in portfolio.accounts:
        acct = db.get(PaperAccount, account["id"])
        nodes = capital.current(db, acct)
        for dep in db.scalars(
            select(PaperDeployment).where(
                PaperDeployment.owner_id == owner.id, PaperDeployment.account_id == acct.id
            )
        ):
            scope = dep.payload.get("capital_scope_id")
            if scope and any(
                row.scope_id in portfolio.allocation_ids for row in capital.chain(nodes, scope)
            ):
                ids.add(dep.id)
    return ids


def receipt_view(row):
    checked(row)
    return PortfolioReceipt(
        id=row.id,
        portfolio_id=row.portfolio_id,
        turn_id=row.turn_id,
        action=row.action,
        result=row.payload["result"],
        checksum=row.checksum,
        created_at=row.created_at,
    )


def context(db, owner, portfolio_id):
    portfolio = view(db, owner, portfolio_id)
    allowed = deployment_ids(db, owner, portfolio)
    allocations, positions = [], []
    for account_id in sorted({row["id"] for row in portfolio.accounts}):
        acct = paper.account(db, owner, account_id)
        nodes = capital.current(db, acct)
        allocations.extend(
            v.model_dump(mode="json")
            for v in capital.views(db, acct)
            if any(row.scope_id in portfolio.allocation_ids for row in capital.chain(nodes, v.id))
        )
        positions.extend(
            paper.position_view(db, entry).model_dump(mode="json")
            for entry in paper.ledger(db, acct)[2].values()
            if entry.deployment_id in allowed
        )
    deps = [
        paper.deployment_view(paper.deployment(db, owner, key), db).model_dump(mode="json")
        for key in sorted(allowed)
    ]
    receipts = [
        receipt_view(row)
        for row in db.scalars(
            select(PortfolioActionReceipt)
            .where(
                PortfolioActionReceipt.owner_id == owner.id,
                PortfolioActionReceipt.portfolio_id == portfolio.id,
            )
            .order_by(PortfolioActionReceipt.created_at.desc())
            .limit(25)
        )
    ]
    from . import analysis

    jobs = db.scalars(
        select(ChatResearch.job_id)
        .join(ChatTurn, ChatTurn.id == ChatResearch.turn_id)
        .where(ChatTurn.owner_id == owner.id, ChatTurn.portfolio_id == portfolio.id)
        .distinct()
        .limit(25)
    )
    research = [
        analysis.view(db, analysis.get_job(db, owner, key)).model_dump(mode="json") for key in jobs
    ]
    from . import portfolio_research, qualification

    research_state = portfolio_research.state(db, owner, portfolio.id)
    return PortfolioContext(
        portfolio=portfolio,
        allocations=allocations,
        deployments=deps,
        positions=positions,
        research_jobs=research,
        receipts=receipts,
        as_of=now(),
        daily_research={
            "revision": research_state.revision,
            "version_id": research_state.version_id,
            "parameters": research_state.parameters.model_dump(mode="json")
            if research_state.parameters
            else None,
            "worker_ready": research_state.worker_ready,
            "runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "trigger": run.trigger,
                    "recorded_steps": len(run.steps),
                    "due_at": run.due_at.isoformat(),
                    "native_jobs": run.native_jobs,
                    "digest_available": run.digest is not None,
                }
                for run in research_state.runs
            ],
            "next_daily_due": research_state.next_daily_due.isoformat()
            if research_state.next_daily_due
            else None,
        },
        research_digests=[r.digest for r in research_state.runs if r.digest][:5],
        strategy_qualification=qualification.portfolio_summary(db, owner, portfolio.id),
    )


def chat_action(db, owner, turn, body):
    from .portfolio_contracts import PortfolioSave

    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    if not turn or not turn.portfolio_id:
        raise HTTPException(403, "Open a portfolio conversation to use portfolio actions.")
    portfolio = view(db, owner, turn.portfolio_id)
    request_id = str(
        uuid5(NAMESPACE_URL, f"tauric-portfolio:{body.session_key}:{body.tool_call_id}")
    )
    request = body.model_dump(mode="json")
    old = db.scalar(
        select(PortfolioActionReceipt).where(
            PortfolioActionReceipt.owner_id == owner.id,
            PortfolioActionReceipt.request_id == request_id,
        )
    )
    if old:
        if checked(old).payload["request"] != request or old.portfolio_id != portfolio.id:
            raise HTTPException(409, "This tool call already performed a different action.")
        return receipt_view(old)
    preferences = portfolio.preferences
    if body.action == "preferences":
        if not preferences or not preferences.chat_edit_preferences:
            raise HTTPException(403, "Portfolio preference edits through chat are disabled.")
        if (
            body.research_parameters is not None
            or body.deployment_id is not None
            or (body.mandate is None and body.research_objective is None)
        ):
            raise HTTPException(422, "Specify a mandate or research objective change.")
        values = preferences.model_dump()
        values.update(
            {
                key: getattr(body, key)
                for key in ("mandate", "research_objective")
                if getattr(body, key) is not None
            }
        )
        changed = save(
            db,
            owner,
            portfolio.id,
            PortfolioSave(
                request_id=request_id,
                expected_revision=body.expected_revision,
                preferences=PortfolioPreferences.model_validate(values),
                allocation_ids=portfolio.allocation_ids,
                reason=body.reason,
            ),
            commit=False,
            turn_id=turn.id,
        )
        result = {
            "before": portfolio.model_dump(mode="json"),
            "after": changed.model_dump(mode="json"),
        }
    elif body.action == "research_settings":
        from . import portfolio_research
        from .portfolio_research_contracts import PortfolioResearchSave

        if not preferences or not preferences.chat_edit_preferences:
            raise HTTPException(
                403, "Portfolio research preference edits through chat are disabled."
            )
        if body.research_parameters is None or any(
            v is not None for v in (body.mandate, body.research_objective, body.deployment_id)
        ):
            raise HTTPException(
                422, "Provide only the portfolio research parameters for this action."
            )
        before = portfolio_research.state(db, owner, portfolio.id)
        after = portfolio_research.save(
            db,
            owner,
            portfolio.id,
            PortfolioResearchSave(
                request_id=request_id,
                expected_revision=body.expected_revision,
                parameters=body.research_parameters,
                reason=body.reason,
            ),
            commit=False,
            turn_id=turn.id,
        )
        result = {
            "before": {
                "revision": before.revision,
                "parameters": before.parameters.model_dump(mode="json")
                if before.parameters
                else None,
            },
            "after": {
                "revision": after.revision,
                "parameters": after.parameters.model_dump(mode="json"),
            },
            "shared_budget_unchanged": True,
            "execution_authority": False,
        }
    else:
        if not preferences or not preferences.chat_control_deployments:
            raise HTTPException(403, "Deployment controls through portfolio chat are disabled.")
        if (
            body.research_parameters is not None
            or body.mandate is not None
            or body.research_objective is not None
        ):
            raise HTTPException(422, "Deployment controls cannot change research preferences.")
        dep_id = str(body.deployment_id) if body.deployment_id else ""
        if dep_id not in deployment_ids(db, owner, portfolio):
            raise HTTPException(404, "Deployment not found in this portfolio.")
        dep = paper.deployment(db, owner, dep_id)
        before = strategies.control(db, dep)
        after = strategies.change_control(
            db,
            owner,
            dep_id,
            DeploymentControl(
                expected_revision=body.expected_revision,
                enabled=body.action == "resume",
                reason=body.reason,
            ),
            commit=False,
        )
        result = {
            "deployment_id": dep_id,
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
            "orders_created": False,
            "positions_preserved": True,
        }
    payload = {"request": request, "result": result}
    receipt = PortfolioActionReceipt(
        id=identifier(),
        owner_id=owner.id,
        portfolio_id=portfolio.id,
        turn_id=turn.id,
        request_id=request_id,
        action=body.action,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(receipt)
    services.audit(
        db,
        owner,
        "portfolio.chat_action",
        "chat",
        turn.id,
        body.reason,
        {"portfolio_id": portfolio.id, "receipt_id": receipt.id, "action": body.action},
    )
    db.commit()
    return receipt_view(receipt)
