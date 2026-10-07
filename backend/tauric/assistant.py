"""Bounded manager operations; native reports, approvals, orders and source stay read-only.

Effects use the existing owner services. Stable request IDs recover a lost
receipt without repeating an effect. Receipts are immutable database history.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import (
    analytics,
    candidate_research,
    chat,
    chat_research,
    knowledge,
    paper,
    research,
    scanner,
    services,
    strategies,
)
from .assistant_contracts import (
    RESEARCH_CONTRACTS,
    AssistantReceipt,
    AssistantState,
    CandidateManagement,
    CandidateResearch,
    ResearchNote,
    StrategyRevision,
    UpgradeProposal,
)
from .models import (
    AssistantRecord,
    MarketSnapshot,
    ScannerCandidate,
    ScannerProfileVersion,
    StrategyVersion,
    now,
)
from .scanner_contracts import CandidateAction, CandidateEscalate, ScannerRunRequest, ScannerSave
from .strategy_contracts import StrategyCreate, StrategyRevise

CONTRACTS = {
    "save_scanner": ScannerSave,
    "run_scanner": ScannerRunRequest,
    "candidate_action": CandidateManagement,
    "research_candidate": CandidateResearch,
    "create_strategy": StrategyCreate,
    "revise_strategy": StrategyRevision,
    **RESEARCH_CONTRACTS,
    "save_research_note": ResearchNote,
    "prepare_upgrade": UpgradeProposal,
}
TITLES = {
    "save_scanner": "Scanner saved",
    "run_scanner": "Scan completed",
    "candidate_action": "Opportunity updated",
    "research_candidate": "Tauric research requested",
    "create_strategy": "Strategy created",
    "revise_strategy": "Strategy version saved",
    "capture_dataset": "Dataset saved",
    "run_backtest": "Backtest completed",
    "save_research_note": "Research note saved",
    "prepare_upgrade": "Upgrade ready for review",
}
SOURCE_PREFIXES = (
    "frontend/src/",
    "backend/tauric/",
    "backend/tests/",
    "docs/",
    "openclaw/plugin/src/",
)
SOURCE_FILES = {
    "frontend/package.json",
    "openclaw/config.mjs",
    "openclaw/package.json",
    "README.md",
}
SOURCE_SUFFIXES = {".py", ".tsx", ".ts", ".css", ".md", ".txt", ".mjs", ".json"}


def authorize(db, owner, body):
    config, _ = chat.load_config(db)
    turn = chat_research.authorize(db, owner, body, config)
    if turn and turn.portfolio_id:
        raise HTTPException(
            403,
            "Use a general conversation for project management. Portfolio controls stay scoped to their portfolio.",
        )
    return turn


def receipt(row):
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Assistant receipt integrity failed.")
    return AssistantReceipt(
        id=row.id,
        turn_id=row.turn_id,
        operation=row.operation,
        title=row.title,
        status="review_required" if row.operation == "prepare_upgrade" else "saved",
        result=row.payload["result"],
        checksum=row.checksum,
        created_at=row.created_at,
    )


def history(db, owner_id, turn_id=None):
    query = select(AssistantRecord).where(
        AssistantRecord.owner_id == owner_id, AssistantRecord.stage == "completed"
    )
    if turn_id:
        query = query.where(AssistantRecord.turn_id == turn_id)
    return [
        receipt(r)
        for r in db.scalars(
            query.order_by(AssistantRecord.created_at.desc(), AssistantRecord.id.desc()).limit(30)
        )
    ]


def state(db, owner):
    return AssistantState(receipts=history(db, owner.id))


def source_root():
    packaged = Path("/app/project-source")
    local = Path(__file__).resolve().parents[2]
    return local if not packaged.is_dir() and (local / "backend/tauric").is_dir() else packaged


def source_path(value):
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part.startswith(".") for part in path.parts)
        or ".." in path.parts
        or "\\" in value
        or value != path.as_posix()
        or path.suffix not in SOURCE_SUFFIXES
        or not (value in SOURCE_FILES or value.startswith(SOURCE_PREFIXES))
    ):
        raise HTTPException(422, "Choose a project source file from the supplied source index.")
    root = source_root().resolve()
    target = root.joinpath(*path.parts)
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise HTTPException(422, "Source links outside the project are not readable.")
    return target


def file_hash(path):
    if not path.is_file():
        return None
    if path.stat().st_size > 500000:
        raise HTTPException(422, "This source file exceeds the reading limit.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_index():
    root = source_root().resolve()
    values = set(SOURCE_FILES)
    for prefix in SOURCE_PREFIXES:
        folder = root / prefix
        if folder.is_dir():
            values.update(
                str(p.relative_to(root))
                for p in folder.rglob("*")
                if p.is_file()
                and not p.is_symlink()
                and p.resolve().is_relative_to(root)
                and p.suffix in SOURCE_SUFFIXES
                and "__pycache__" not in p.parts
            )
    return sorted(v for v in values if source_path(v).is_file())[:600]


def summary_result(value):
    data = value.model_dump(mode="json")
    if "result" in data and "trades" in data["result"]:
        # Full replay history stays accessible through the owned research resource.
        data["result"] = {
            k: v
            for k, v in data["result"].items()
            if k not in {"equity", "trades", "fills", "signals", "financing_events"}
        }
    return data


def read(db, owner, body):
    authorize(db, owner, body)
    if body.resource in {"knowledge", "user_memory"}:
        allowed, detail = knowledge.recall_allowed(db, owner)
        if not allowed:
            raise HTTPException(403, detail)
    if body.resource == "knowledge":
        if body.resource_id:
            row = knowledge.owned(db, owner, body.resource_id)
            from .honcho_memory import selected

            if not selected(row, knowledge.settings(db, owner.id)[0]):
                raise HTTPException(404, "This note is hidden from wiki recall.")
            return knowledge.view(row).model_dump(mode="json")
        return knowledge.search(db, owner, body.query, body.kind, limit=8, excerpt=2400).model_dump(
            mode="json"
        )
    if body.resource == "user_memory":
        value = knowledge.state(db, owner).model_dump(mode="json")
        return {
            key: value[key]
            for key in ("settings", "counts", "honcho_representation", "honcho_detail", "authority")
        }
    if body.resource == "performance":
        value = analytics.view(db, owner, str(body.resource_id) if body.resource_id else None)
        return value.model_dump(mode="json")
    if body.resource == "schemas":
        operations = [body.operation] if body.operation else list(CONTRACTS)
        result = {}
        for operation in operations:
            schema = CONTRACTS[operation].model_json_schema()
            for key in ("request_id", "reason"):
                schema["properties"].pop(key, None)
                if key in schema.get("required", []):
                    schema["required"].remove(key)
            result[operation] = schema
        return {
            "parameters_schemas": result,
            "request_id": "assigned by Tauric",
            "reason": "required on the action itself",
        }
    if body.resource == "source":
        if not body.path:
            return {
                "files": source_index(),
                "authority": "read_only",
                "runtime_secrets_available": False,
            }
        target = source_path(body.path)
        digest = file_hash(target)
        if digest is None:
            raise HTTPException(404, "Project source file not found.")
        lines = target.read_text().splitlines()
        return {
            "path": body.path,
            "sha256": digest,
            "total_lines": len(lines),
            "start_line": body.start_line,
            "lines": lines[body.start_line - 1 : body.start_line - 1 + body.line_count],
            "authority": "read_only",
        }
    if body.resource in {"dataset", "backtest"}:
        if not body.resource_id:
            raise HTTPException(422, "Choose a saved research resource.")
        kind = "dataset" if body.resource == "dataset" else "run"
        row = research.owned(db, owner, str(body.resource_id), kind)
        if kind == "run":
            return summary_result(research.run_view(row))
        view = research.dataset_view(row).model_dump(mode="json")
        return {
            **view,
            "candles": row.payload["candles"][:250],
            "candles_truncated": len(row.payload["candles"]) > 250,
        }
    if body.resource == "receipt":
        row = db.get(AssistantRecord, str(body.resource_id))
        if not row or row.owner_id != owner.id or row.stage != "completed":
            raise HTTPException(404, "Assistant receipt not found.")
        return receipt(row).model_dump(mode="json")
    if body.resource in {"scanner", "strategy", "candidate"}:
        if not body.resource_id:
            raise HTTPException(422, "Choose a saved project resource.")
        if body.resource == "strategy":
            return summary_result(strategies.view(strategies.version(db, owner, body.resource_id)))
        model = ScannerProfileVersion if body.resource == "scanner" else ScannerCandidate
        row = scanner.owned(db, model, owner, str(body.resource_id))
        view = (
            scanner.profile_view(db, owner, row)
            if body.resource == "scanner"
            else scanner.candidate_view(db, owner, row)
        )
        return summary_result(view)
    saved = research.state(db, owner)
    versions = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.owner_id == owner.id)
        .order_by(StrategyVersion.created_at.desc())
        .limit(26)
    )
    snapshots = db.scalars(
        select(MarketSnapshot).order_by(MarketSnapshot.received_at.desc()).limit(25)
    )
    version_values = [strategies.view(v).model_dump(mode="json") for v in versions]
    for v in version_values:
        description = v["parameters"]["description"]
        v["description_truncated"] = len(description) > 500
        v["parameters"]["description"] = description[:500]
    scan_state = scanner.state(db, owner).model_dump(mode="json")
    profiles, candidates = scan_state["profiles"], scan_state["candidates"]
    scan_state["profiles_truncated"] = len(profiles) > 10
    scan_state["profiles"] = profiles[:10]
    for p in scan_state["profiles"]:
        p["universe_truncated"] = len(p["parameters"]["universe"]) > 100
        p["parameters"]["universe"] = p["parameters"]["universe"][:100]
    scan_state["candidates_truncated"] = len(candidates) > 25
    scan_state["candidates"] = [
        {
            k: c[k]
            for k in (
                "id",
                "instrument_id",
                "asset_class",
                "interval",
                "status",
                "setup_score",
                "detector_id",
                "direction",
                "expires_at",
            )
        }
        for c in candidates[:25]
    ]
    scan_state["runs"] = [
        {k: r[k] for k in ("id", "profile_version_id", "created_at", "metrics")}
        for r in scan_state["runs"][:10]
    ]
    authority = state(db, owner).model_dump(mode="json")
    authority["receipts"] = [
        {k: r[k] for k in ("id", "title", "operation", "status", "created_at")}
        for r in authority["receipts"][:10]
    ]
    return {
        "as_of": now().isoformat(),
        "authority": authority,
        "operations": list(CONTRACTS),
        "scanner": scan_state,
        "strategy_versions": version_values[:25],
        "strategy_versions_truncated": len(version_values) > 25,
        "detail_reading": "Read the individual scanner/strategy/candidate resource before making changes; overview values may be truncated.",
        "datasets": [d.model_dump(mode="json") for d in saved.datasets],
        "backtests": [summary_result(r) for r in saved.runs],
        "market_snapshots": [
            {
                "id": s.id,
                "instrument_id": s.instrument_id,
                "interval": s.interval,
                "received_at": s.received_at.isoformat(),
            }
            for s in snapshots
        ],
        "research_capabilities": chat_research.capabilities(db),
        "quant_workflow": [
            "State a testable hypothesis",
            "Read source and immutable evidence",
            "Compare with a simple baseline",
            "Check costs, leakage and counter-evidence",
            "Save an experiment and decision",
            "Prepare upgrades for owner review",
        ],
        "source_reading_available": bool(source_index()),
        "execution_authority": False,
        "native_reports_writable": False,
        "rl_status": "research_only: execution realism, independent validation and forward evidence are prerequisites",
    }


def execute(db, owner, body):
    turn = authorize(db, owner, body)
    if len(json.dumps(body.parameters)) > 100000:
        raise HTTPException(422, "Keep management requests below 100 KB.")
    if {"request_id", "reason"} & body.parameters.keys():
        raise HTTPException(422, "Tauric assigns request identities; put the reason on the action.")
    request_id = str(
        uuid5(
            NAMESPACE_URL,
            "tauric-manager:" + owner.id + ":" + body.session_key + ":" + body.tool_call_id,
        )
    )
    inputs = {"operation": body.operation, "parameters": body.parameters, "reason": body.reason}
    previous = db.scalar(
        select(AssistantRecord).where(
            AssistantRecord.owner_id == owner.id,
            AssistantRecord.request_id == request_id,
            AssistantRecord.stage == "completed",
        )
    )
    if previous:
        if previous.payload["inputs"] != inputs:
            raise HTTPException(
                409, "This tool call already belongs to different management inputs."
            )
        return receipt(previous)
    values = {**body.parameters, "request_id": request_id}
    if body.operation != "run_scanner":
        values["reason"] = body.reason
    try:
        params = CONTRACTS[body.operation].model_validate(values)
    except ValidationError as exc:
        problems = "; ".join(
            ".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False)
        )
        raise HTTPException(422, "Check these management fields: " + problems) from exc
    operation = body.operation
    # Reserve immutable inputs before a service can commit. A crash after its
    # effect cannot let a different operation reuse the same invocation identity.
    intent_id = str(uuid5(NAMESPACE_URL, "tauric-manager-intent:" + request_id))
    intent = db.get(AssistantRecord, intent_id)
    if intent:
        if paper.checksum(intent.payload) != intent.checksum or intent.payload["inputs"] != inputs:
            raise HTTPException(
                409, "This tool call already belongs to different management inputs."
            )
    else:
        payload = {"inputs": inputs, "result": {}, "channel": body.channel}
        intent = AssistantRecord(
            id=intent_id,
            request_id=request_id,
            stage="requested",
            owner_id=owner.id,
            turn_id=turn.id if turn else None,
            operation=operation,
            title=TITLES[operation],
            payload=payload,
            checksum=paper.checksum(payload),
        )
        db.add(intent)
        services.audit(
            db,
            owner,
            "assistant.requested",
            "assistant_request",
            intent_id,
            body.reason,
            {"operation": operation, "execution_authority": False},
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            intent = db.get(AssistantRecord, intent_id)
            if not intent or intent.payload["inputs"] != inputs:
                raise
    # Recheck the lease after reserving the request and immediately before effect.
    turn = authorize(db, owner, body)
    if operation == "save_scanner":
        result = summary_result(scanner.save(db, owner, params))
    elif operation == "run_scanner":
        if params.trigger != "manual":
            raise HTTPException(
                422,
                "The assistant requests manual scans; scheduled runs belong to the scanner worker.",
            )
        result = summary_result(scanner.run(db, owner, params))
    elif operation == "candidate_action":
        result = summary_result(
            scanner.candidate_action(
                db,
                owner,
                str(params.candidate_id),
                CandidateAction(**params.model_dump(exclude={"candidate_id"})),
            )
        )
    elif operation == "research_candidate":
        result = summary_result(
            candidate_research.escalate(
                db,
                owner,
                str(params.candidate_id),
                CandidateEscalate(**params.model_dump(exclude={"candidate_id"})),
            )
        )
    elif operation == "create_strategy":
        result = summary_result(strategies.save(db, owner, params))
    elif operation == "revise_strategy":
        result = summary_result(
            strategies.save(
                db,
                owner,
                StrategyRevise(**params.model_dump(exclude={"strategy_id"})),
                str(params.strategy_id),
            )
        )
    elif operation == "capture_dataset":
        result = summary_result(research.capture(db, owner, params))
    elif operation == "run_backtest":
        result = summary_result(research.run(db, owner, params))
    else:
        result = params.model_dump(mode="json", exclude={"request_id", "reason"})
        if operation == "prepare_upgrade":
            for ref in params.files:
                if file_hash(source_path(ref.path)) != ref.sha256:
                    raise HTTPException(
                        409,
                        "A proposed source file changed. Read its current checksum before preparing the upgrade.",
                    )
            if params.patch:
                declared = {ref.path for ref in params.files}
                headers = [
                    line[4:].split("\t", 1)[0]
                    for line in params.patch.splitlines()
                    if line.startswith(("--- ", "+++ "))
                ]
                if not headers:
                    raise HTTPException(
                        422,
                        "Provide a unified diff with declared source file headers, or omit the patch.",
                    )
                for value in headers:
                    if value == "/dev/null":
                        continue
                    path = value[2:] if value.startswith(("a/", "b/")) else value
                    source_path(path)
                    if path not in declared:
                        raise HTTPException(
                            422, "Every patch target must be listed with its source checksum."
                        )
            result.update(
                code_changes_applied=False, validation="not_run", approval="owner_review_required"
            )
        else:
            result["evidence_status"] = "assistant_research_note: does not replace native evidence"
    row = AssistantRecord(
        id=request_id,
        request_id=request_id,
        stage="completed",
        owner_id=owner.id,
        turn_id=turn.id if turn else None,
        operation=operation,
        title=result.get("title", TITLES[operation]),
        payload={"inputs": inputs, "result": result, "channel": body.channel},
        checksum="",
    )
    row.checksum = paper.checksum(row.payload)
    db.add(row)
    services.audit(
        db,
        owner,
        "assistant." + operation,
        "assistant_receipt",
        row.id,
        body.reason,
        {"checksum": row.checksum, "execution_authority": False, "source_applied": False},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.scalar(
            select(AssistantRecord).where(
                AssistantRecord.owner_id == owner.id,
                AssistantRecord.request_id == request_id,
                AssistantRecord.stage == "completed",
            )
        )
        if not previous or previous.payload["inputs"] != inputs:
            raise
        return receipt(previous)
    return receipt(row)
