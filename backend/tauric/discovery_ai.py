"""Optional bounded structured ideas using saved compatible connections and priced reservations."""

import json
from decimal import Decimal

from fastapi import HTTPException
from pydantic import Field, ValidationError
from sqlalchemy import select

from . import discovery_journal as journal
from .discovery_contracts import ResearchStrategy
from .discovery_models import DiscoveryRecord
from .discovery_transport import ProviderFailure, request_json
from .model_profiles import load_profiles
from .models import now
from .schemas import Contract


class Idea(Contract):
    instrument_id: str = Field(max_length=120)
    interval: str = Field(max_length=8)
    hypothesis: str = Field(min_length=10, max_length=1200)
    definition: ResearchStrategy


class Ideas(Contract):
    ideas: list[Idea] = Field(min_length=1, max_length=20)


def validate_profile(db, p):
    profiles, _ = load_profiles(db)
    profile = profiles.get(p.ai_profile)
    if not profile or not profile.configured():
        raise HTTPException(
            422, "Configure the selected AI connection before enabling automatic ideas"
        )
    if profile.provider not in {"openai", "openai_compatible", "openrouter", "ollama"}:
        raise HTTPException(
            422, "Automatic priced ideas currently require an OpenAI-compatible connection"
        )
    return profile


def charged(db, owner):
    rows = db.scalars(
        select(DiscoveryRecord).where(
            DiscoveryRecord.owner_id == owner.id,
            DiscoveryRecord.kind == "ai_usage",
            DiscoveryRecord.created_at >= now().replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    return sum(
        (
            Decimal(
                journal.checked(r).payload.get("charged_usd") or r.payload["input"]["reserved_usd"]
            )
            for r in rows
        ),
        Decimal(0),
    )


def generate(db, owner, campaign, frozen):
    from .discovery import head, identity, parameters

    journal.lock(db, owner)
    current = parameters(head(db, owner))
    if not current.ai_enabled or Decimal(current.daily_ai_budget_usd) <= 0:
        raise HTTPException(409, "Automatic AI ideas are paused or have no saved budget")
    request_id = identity(campaign.id + ":ai-ideas")
    old = db.scalar(
        select(DiscoveryRecord).where(
            DiscoveryRecord.owner_id == owner.id,
            DiscoveryRecord.kind == "ai_usage",
            DiscoveryRecord.request_id == str(request_id),
        )
    )
    if old:
        journal.checked(old)
        if old.payload["status"] == "completed":
            return {"usage_id": old.id, "ideas": old.payload["ideas"]}
        raise HTTPException(
            409,
            "The original AI request has an uncertain or failed outcome; it will not be billed again automatically",
        )
    profile = validate_profile(db, current)
    recent = list(
        db.scalars(
            select(DiscoveryRecord)
            .where(DiscoveryRecord.owner_id == owner.id, DiscoveryRecord.kind == "trial")
            .order_by(DiscoveryRecord.created_at.desc())
            .limit(6)
        )
    )
    feedback = [
        {
            "definition": r.payload["input"]["definition"],
            "analysis": {
                k: v for k, v in (r.payload.get("analysis") or {}).items() if k != "daily_returns"
            },
        }
        for r in recent
    ]
    prompt = (
        "Propose falsifiable research hypotheses as JSON only. No code, orders, approvals or promises of returns. Use exactly the provided schema and supported instruments, intervals and families. Weights sum to one. A context interval must be higher than the entry interval. Explain what could invalidate each hypothesis. Return at most "
        + str(campaign.payload["planned_hypotheses"])
        + " ideas.\n"
        + json.dumps(
            {
                "schema": Ideas.model_json_schema(),
                "instruments": frozen.instrument_ids,
                "intervals": frozen.intervals,
                "families": frozen.families,
                "multi_timeframe": frozen.multi_timeframe,
                "mixtures": frozen.mixtures,
                "prior_experiments": feedback,
            },
            separators=(",", ":"),
        )
    )
    if len(prompt.encode()) > 24000:
        raise HTTPException(422, "Research idea context exceeded the bounded prompt size")
    # UTF-8 bytes plus framing form a conservative input allowance for the supported text protocol.
    input_ceiling = len(prompt.encode()) + 2048
    input_price, output_price = (
        Decimal(current.ai_input_usd_per_million),
        Decimal(current.ai_output_usd_per_million),
    )
    output_ceiling = current.ai_max_output_tokens
    reserved = (input_ceiling * input_price + output_ceiling * output_price) / 1000000
    ceiling = min(Decimal(current.daily_ai_budget_usd), Decimal(frozen.daily_ai_budget_usd))
    if charged(db, owner) + reserved > ceiling:
        raise HTTPException(
            429, "Today's AI budget cannot cover the reserved maximum; no model call was made"
        )
    usage = journal.append(
        db,
        owner,
        "ai_usage",
        request_id,
        {"reason": "Reserve one bounded strategy idea request"},
        parent_id=campaign.id,
        inputs={
            "reserved_usd": str(reserved),
            "input_ceiling": input_ceiling,
            "output_ceiling": output_ceiling,
            "input_price": str(input_price),
            "output_price": str(output_price),
            "profile_fingerprint": profile.fingerprint(),
            "model": profile.quick_model,
        },
        status="running",
        charged_usd=None,
        ideas=None,
        error=None,
    )
    db.commit()  # The reservation survives a timeout or process crash; uncertain requests are never retried.
    base = profile.base_url.rstrip("/") or {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }.get(profile.provider, "")
    if not base:
        raise HTTPException(422, "The selected compatible connection needs an endpoint")
    headers = {"Content-Type": "application/json"}
    if profile.api_key.get_secret_value():
        headers["Authorization"] = "Bearer " + profile.api_key.get_secret_value()
    body = {
        "model": profile.quick_model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "n": 1,
        ("max_completion_tokens" if profile.provider == "openai" else "max_tokens"): output_ceiling,
    }
    diagnostic = {}
    stage = "provider"
    try:
        payload, diagnostic = request_json(base + "/chat/completions", headers, body)
        stage = "usage"
        tokens = payload["usage"]
        inputs, outputs = tokens["prompt_tokens"], tokens["completion_tokens"]
        if type(inputs) is not int or type(outputs) is not int or inputs < 0 or outputs < 0:
            raise ValueError("Invalid usage")
        actual = (inputs * input_price + outputs * output_price) / 1000000
        stage = "ideas_schema"
        ideas = Ideas.model_validate_json(payload["choices"][0]["message"]["content"])
        stage = "ideas_scope"
        if len(ideas.ideas) > campaign.payload["planned_hypotheses"]:
            raise ValueError("Too many ideas")
        from .market import INTERVAL_SECONDS

        for idea in ideas.ideas:
            d = idea.definition
            if (
                idea.instrument_id not in frozen.instrument_ids
                or idea.interval not in frozen.intervals
                or any(c.family not in frozen.families for c in d.components)
            ):
                raise ValueError("Idea outside saved research scope")
            if d.context_interval and (
                not frozen.multi_timeframe
                or d.context_interval not in frozen.intervals
                or INTERVAL_SECONDS[d.context_interval] <= INTERVAL_SECONDS[idea.interval]
            ):
                raise ValueError("Invalid context timeframe")
            if d.mode != "single" and not frozen.mixtures:
                raise ValueError("Mixtures disabled")
        if inputs > input_ceiling or outputs > output_ceiling or actual > reserved:
            journal.update(
                usage,
                status="failed",
                charged_usd=str(actual),
                error="Provider usage exceeded the reserved token allowance",
            )
            db.commit()
            raise HTTPException(
                409,
                "Provider usage exceeded the reserved allowance; inspect the saved usage record",
            )
        value = ideas.model_dump(mode="json")["ideas"]
        journal.update(
            usage,
            status="completed",
            charged_usd=str(actual),
            ideas=value,
            prompt_tokens=inputs,
            completion_tokens=outputs,
            diagnostic=diagnostic,
        )
        db.commit()
        return {"usage_id": usage.id, "ideas": value}
    except (ProviderFailure, KeyError, ValueError, TypeError) as exc:
        diagnostic = {**diagnostic, "category": stage}
        if isinstance(exc, ProviderFailure):
            diagnostic = exc.diagnostic
        elif isinstance(exc, ValidationError):
            schema = Ideas.model_json_schema()
            allowed = set(schema.get("properties", {}))
            for definition in schema.get("$defs", {}).values():
                allowed.update(definition.get("properties", {}))
            diagnostic["schema_paths"] = [
                ".".join(str(part) if isinstance(part, int) or part in allowed else "unrecognized_field"
                         for part in error["loc"])[:160]
                for error in exc.errors(include_input=False)[:10]
            ]
        journal.update(
            usage,
            status="failed",
            error="Model output or usage could not be verified; the full reservation remains charged to the research ceiling",
            diagnostic=diagnostic,
        )
        db.commit()
        raise HTTPException(
            409,
            "The AI idea request failed verification; its reservation was retained and it will not be retried automatically",
        ) from None
