"""One real bound model review against an isolated paper ledger; never broker orders."""

import hashlib
import json
import os
import subprocess
import sys
import time
from decimal import Decimal, localcontext
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from tauric.models import AnalysisArtifact, GovernanceJob, TradingRecord

REPORT_FIELDS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
    "research_debate",
    "risk_debate",
)


def verify_journal(
    state, account_id, proposal, decision, plan, entry, exit_fill, original_id
):
    """Reconstruct a closed position from immutable records, independently of ledger output."""
    records = {v["id"]: v for v in state["records"]}
    for record in (proposal, decision, plan, entry, exit_fill):
        assert records[record["id"]] == record
        assert record["account_id"] == account_id
        assert (
            hashlib.sha256(
                json.dumps(
                    record["payload"],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            == record["checksum"]
        )
    assert decision["payload"]["source"] == "openclaw"
    assert decision["payload"]["outcome"] == "allow"
    assert decision["payload"]["proposal_id"] == proposal["id"]
    assert plan["payload"]["decision_id"] == decision["id"]
    assert entry["payload"]["plan_id"] == plan["id"]
    assert entry["payload"]["decision_id"] == decision["id"]
    assert exit_fill["payload"]["position_id"] == entry["id"]
    assert exit_fill["payload"]["opening_decision_id"] == decision["id"]
    assert entry["payload"]["side"] == "buy" and exit_fill["payload"]["side"] == "sell"
    for record in (proposal, decision, plan, entry, exit_fill):
        assert record["payload"]["native_artifact_id"] == original_id
    for record in (entry, exit_fill):
        assert record["payload"]["simulated"] is True
        assert record["payload"]["lineage"] == plan["payload"]["lineage"]
    fills = [
        v
        for v in state["records"]
        if v["kind"] == "fill" and v["account_id"] == account_id
    ]
    assert {v["id"] for v in fills} == {entry["id"], exit_fill["id"]}
    account = next(v for v in state["accounts"] if v["id"] == account_id)
    assert account["reconciled"] and not [
        v for v in state["positions"] if v["account_id"] == account_id
    ]
    with localcontext() as ctx:
        ctx.prec = 120
        p, x = entry["payload"], exit_fill["payload"]
        quantity = Decimal(p["quantity"])
        assert quantity == Decimal(x["quantity"])
        entry_delta = -(quantity * Decimal(p["price"]) + Decimal(p["fee"]))
        exit_delta = quantity * Decimal(x["price"]) - Decimal(x["fee"])
        pnl = entry_delta + exit_delta
        assert Decimal(p["cash_delta"]) == entry_delta
        assert Decimal(x["cash_delta"]) == exit_delta
        assert Decimal(x["realized_pnl"]) == pnl == Decimal(account["realized_pnl"])
        assert Decimal(account["cash"]) == Decimal(account["starting_balance"]) + pnl
    return {
        "cash": account["cash"],
        "realized_pnl": account["realized_pnl"],
        "fill_count": 2,
    }


def action(reason="Disposable real-model governance verification"):
    return {"request_id": str(uuid4()), "reason": reason}


def main():
    fixture = json.loads((root / "artifacts/browser-test.json").read_text())
    info = json.loads((root / "artifacts/real-governance-input.json").read_text())
    if (
        not fixture["database_name"].startswith("tauric_browser_")
        or info.get("fixture") is not True
    ):
        raise RuntimeError("A disposable verification database is required.")
    url = urlsplit(fixture["database_url"])
    env = os.environ.copy()
    env["BROWSER_DATABASE_URL"] = urlunsplit(
        (
            url.scheme,
            url.netloc.rsplit("@", 1)[0] + "@db:5432",
            url.path,
            url.query,
            url.fragment,
        )
    )
    compose = ["docker", "compose", "-f", str(root / "deploy/compose.browser.yml")]

    def command(*args):
        r = subprocess.run([*compose, *args], env=env, capture_output=True, check=False)
        if r.returncode:
            raise RuntimeError(
                "Private verification services failed to start; no credentials printed."
            )

    result = {
        "verification_scope": "Real tool/decision transport on a disposable paper ledger with unchanged real native research",
        "source_artifact_id": info.get("source_artifact_id"),
        "source_checksum": info.get("source_checksum"),
        "model": info["model"],
        "fresh_analysis": info.get("fresh_analysis", False),
        "broker_order_calls": 0,
        "owner_paper_account_changes": 0,
    }
    try:
        services = ["test_api", "web", "test_openclaw", "test_governance_worker"]
        if result["fresh_analysis"]:
            services.append("test_native_worker")
        command(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "60",
            *services,
        )
        with httpx.Client(
            base_url="http://127.0.0.1:8081", timeout=30, trust_env=False
        ) as client:
            login = client.post(
                "/api/v1/session",
                headers={"Origin": "http://127.0.0.1:8081"},
                json={"username": fixture["username"], "password": fixture["password"]},
            )
            if login.status_code != 200:
                raise RuntimeError("Disposable owner login failed.")
            client.headers.update(
                {
                    "Origin": "http://127.0.0.1:8081",
                    "X-CSRF-Token": login.json()["csrf_token"],
                }
            )

            def api(path, body=None, method="POST"):
                r = client.request(
                    method if body is not None else "GET", path, json=body
                )
                if r.status_code not in [200, 201, 202]:
                    raise RuntimeError(
                        f"Verification API rejected {path}: HTTP {r.status_code}"
                    )
                return r.json()

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                status = api("/api/v1/governance")
                if (
                    status["worker_ready"]
                    and status["gateway_ready"]
                    and next(v for v in status["routes"] if v["id"] == "local")["ready"]
                ):
                    break
                time.sleep(2)
            else:
                raise RuntimeError(
                    "Real-model private review route did not become ready."
                )
            if result["fresh_analysis"]:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if api("/api/v1/analysis/status")["worker_ready"]:
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError("Isolated native worker did not become ready.")
                request = {
                    "request_id": str(uuid4()),
                    "instrument_id": "binance:spot:BTCUSDT",
                    "profile": "local",
                }
                native = api("/api/v1/analysis", request)
                assert api("/api/v1/analysis", request)["id"] == native["id"]
                result["native_job_id"] = native["id"]
                print(
                    "One fresh native BTC analysis queued; no automatic retry.",
                    flush=True,
                )
                deadline = time.monotonic() + 950
                while time.monotonic() < deadline:
                    native = api(f"/api/v1/analysis/{native['id']}")
                    if native["status"] not in ["queued", "running"]:
                        break
                    time.sleep(2)
                result["native_status"] = native["status"]
                if native["status"] != "succeeded":
                    result["native_error"] = native["error"]
                    raise RuntimeError(
                        "Fresh native analysis did not succeed; no retry made."
                    )
                original = api(f"/api/v1/analysis-artifacts/{native['artifact_id']}")
                assert all(
                    isinstance(original["payload"]["reports"].get(k), str)
                    and original["payload"]["reports"][k].strip()
                    for k in REPORT_FIELDS
                )
                assert (
                    original["payload"]["execution_authority"] is False
                    and original["payload"]["governance"] is None
                )
                assert (
                    hashlib.sha256(
                        json.dumps(
                            original["payload"], sort_keys=True, allow_nan=False
                        ).encode()
                    ).hexdigest()
                    == original["checksum"]
                )
                info.update(
                    artifact_id=original["id"],
                    source_artifact_id=original["id"],
                    source_checksum=original["checksum"],
                )
                result.update(
                    source_artifact_id=original["id"],
                    source_checksum=original["checksum"],
                    native_report_count=len(REPORT_FIELDS),
                )
                report_path = root / "artifacts/real-paper-native-report.json"
                report_path.write_text(json.dumps(original, indent=2))
                report_path.chmod(0o600)
                print(
                    "Fresh native analysis succeeded; all nine original reports retained.",
                    flush=True,
                )
            policy = {
                "risk_percent": "0.1",
                "max_total_risk_percent": "0.5",
                "max_exposure_percent": "5",
                "max_positions": 1,
                "fee_bps": "10",
                "slippage_bps": "5",
                "quote_max_age_seconds": 60,
                "decision_ttl_seconds": 300,
            }
            account = api(
                "/api/v1/paper/accounts",
                {
                    **action(),
                    "name": "DISPOSABLE real-model verification account",
                    "currency": "USDT",
                    "starting_balance": "10000",
                    "policy": policy,
                },
            )
            scope = api(
                "/api/v1/paper/capital",
                {
                    **action(),
                    "account_id": account["id"],
                    "parent_id": None,
                    "kind": "portfolio",
                    "name": "DISPOSABLE verification capital",
                    "limits": {
                        "budget": "250",
                        "max_total_risk_percent": "0.5",
                        "max_positions": 1,
                    },
                },
            )
            saved = api(
                "/api/v1/strategies",
                {
                    **action(),
                    "name": "DISPOSABLE real-model verification strategy",
                    "parameters": {
                        "label": "verification-v1",
                        "description": "Disposable daily swing starter strategy, intended horizon 1–3 months, unlevered BTC spot paper only. One entry capped at 250 USDT (2.5% of the fictional 10,000 USDT account); no pyramiding or breakout/add-on entry. Entry eligibility requires supportive current native daily research and a separate fresh Binance BTCUSDT observation. The proposal's USDT stop reference is explicitly set 3.75% below that observation as a simulation assumption, not copied or converted from USD research and not a monitored protective order. Per-trade risk is capped at 0.1% of account equity including modeled costs. The verification closes any simulated position explicitly after checking the fill. No assertion of USD/USDT equivalence, intraday timing, liquidity or profitability is made. Reject or defer inconsistent, missing or expired evidence.",
                        "quantity_step": "0.00001",
                        "capital_limit": "250",
                        "risk_percent": "0.1",
                    },
                },
            )
            p = saved["parameters"]
            dep = api(
                "/api/v1/paper/deployments",
                {
                    **action(),
                    "account_id": account["id"],
                    "instrument_id": "binance:spot:BTCUSDT",
                    "portfolio": scope["name"],
                    "capital_pot": scope["name"],
                    "capital_scope_id": scope["id"],
                    "strategy_revision_id": saved["id"],
                    "strategy": saved["name"],
                    "strategy_version": p["label"],
                    "quantity_step": p["quantity_step"],
                    "capital_limit": p["capital_limit"],
                    "risk_percent": p["risk_percent"],
                },
            )

            def quote():
                return api(
                    "/api/v1/market/binance:spot:BTCUSDT/refresh", {"interval": "1m"}
                )

            q = quote()
            stop = format(
                (Decimal(q["candles"][-1]["close"]) * Decimal("0.9625")).quantize(
                    Decimal("0.01")
                ),
                "f",
            )
            proposal = api(
                "/api/v1/paper/proposals",
                {
                    **action(
                        "Disposable daily swing starter; fresh Binance USDT observation and explicit 3.75% paper stop reference. Original USD research is directional evidence only. Assess strategy and policy; reject/defer gaps. No live orders."
                    ),
                    "deployment_id": dep["id"],
                    "stop_price": stop,
                    "native_artifact_id": info["artifact_id"],
                },
            )
            body = {**action(), "proposal_id": proposal["id"], "profile": "local"}
            review = api("/api/v1/governance/jobs", body)
            assert api("/api/v1/governance/jobs", body)["id"] == review["id"]
            result.update(
                review_job_id=review["id"], context_checksum=review["context_checksum"]
            )
            print(
                "One real-model permission review queued in the disposable ledger; no automatic retry.",
                flush=True,
            )
            deadline = time.monotonic() + 210
            last_quote = time.monotonic()
            while time.monotonic() < deadline:
                review = next(
                    v
                    for v in api("/api/v1/governance")["jobs"]
                    if v["id"] == review["id"]
                )
                if review["status"] not in ["queued", "running"]:
                    break
                if time.monotonic() - last_quote >= 25:
                    quote()
                    last_quote = time.monotonic()
                time.sleep(2)
            result.update(
                status=review["status"],
                reported_usage=review["usage"],
                error=review["error"],
            )
            temporary = create_engine(fixture["database_url"])
            with Session(temporary) as db:
                job = db.get(GovernanceJob, review["id"])
                original = db.get(AnalysisArtifact, info["artifact_id"])
                result["bound_context_read"] = job.context_read_at is not None
                assert original.checksum == info["source_checksum"]
                assert (
                    hashlib.sha256(
                        json.dumps(
                            original.payload, sort_keys=True, allow_nan=False
                        ).encode()
                    ).hexdigest()
                    == original.checksum
                )
                result["original_unchanged"] = True
                if job.decision_id:
                    decision = db.get(TradingRecord, job.decision_id)
                    result["outcome"] = decision.payload["outcome"]
                    result["decision_id"] = decision.id
                    result["decision_reason"] = decision.payload["reason"]
            temporary.dispose()
            if review["status"] == "succeeded":
                assert result["bound_context_read"] and review["decision_id"]
                if result["outcome"] == "allow":
                    decision = api(
                        f"/api/v1/resources/trading_record/{review['decision_id']}"
                    )["data"]
                    quote()
                    plan_body = action()
                    plan = api(
                        f"/api/v1/paper/decisions/{review['decision_id']}/plan",
                        plan_body,
                    )
                    assert (
                        api(
                            f"/api/v1/paper/decisions/{review['decision_id']}/plan",
                            plan_body,
                        )["id"]
                        == plan["id"]
                    )
                    fill_body = action()
                    fill = api(f"/api/v1/paper/plans/{plan['id']}/execute", fill_body)
                    assert (
                        api(f"/api/v1/paper/plans/{plan['id']}/execute", fill_body)[
                            "id"
                        ]
                        == fill["id"]
                    )
                    quote()
                    close_body = action()
                    close = api(
                        f"/api/v1/paper/positions/{fill['id']}/close", close_body
                    )
                    assert (
                        api(f"/api/v1/paper/positions/{fill['id']}/close", close_body)[
                            "id"
                        ]
                        == close["id"]
                    )
                    assert close["payload"]["lineage"] == fill["payload"]["lineage"]
                    state = api("/api/v1/paper")
                    result["journal"] = verify_journal(
                        state,
                        account["id"],
                        proposal,
                        decision,
                        plan,
                        fill,
                        close,
                        info["artifact_id"],
                    )
                    result.update(
                        paper_proposal_id=proposal["id"],
                        paper_plan_id=plan["id"],
                        paper_fill_id=fill["id"],
                        paper_exit_id=close["id"],
                        journal_reconstruction_verified=True,
                        paper_allow_fill_close_verified=True,
                    )
                else:
                    denied = client.post(
                        f"/api/v1/paper/decisions/{review['decision_id']}/plan",
                        json=action(),
                    )
                    assert denied.status_code == 409
                    result.update(
                        blocked_plan_http=denied.status_code,
                        paper_allow_fill_close_verified=False,
                        positive_workflow_verified=False,
                    )
                state = api("/api/v1/paper")
                assert not state["positions"] and all(
                    v["reconciled"] for v in state["accounts"]
                )
                result["paper_reconciled"] = True
                if result["outcome"] != "allow":
                    assert not [
                        v for v in state["records"] if v["kind"] in ("plan", "fill")
                    ]
                original = api(f"/api/v1/analysis-artifacts/{info['artifact_id']}")
                assert original["checksum"] == info["source_checksum"]
                assert (
                    hashlib.sha256(
                        json.dumps(
                            original["payload"], sort_keys=True, allow_nan=False
                        ).encode()
                    ).hexdigest()
                    == info["source_checksum"]
                )
                result["original_unchanged_after_execution"] = True
                command("restart", "test_api")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        state = api("/api/v1/paper")
                        break
                    except (RuntimeError, httpx.HTTPError):
                        time.sleep(2)
                else:
                    raise RuntimeError("API did not recover after restart.")
                if result["outcome"] == "allow":
                    assert (
                        verify_journal(
                            state,
                            account["id"],
                            proposal,
                            decision,
                            plan,
                            fill,
                            close,
                            info["artifact_id"],
                        )
                        == result["journal"]
                    )
                else:
                    assert any(
                        v["id"] == review["decision_id"] for v in state["records"]
                    )
                    assert (
                        all(v["reconciled"] for v in state["accounts"])
                        and not state["positions"]
                    )
                result["api_restart_persistence_verified"] = True
                result["positive_workflow_verified"] = result["outcome"] == "allow"
                evidence = {
                    "native": original,
                    "review": api(f"/api/v1/resources/governance_job/{review['id']}")[
                        "data"
                    ],
                    "paper": state,
                }
                evidence_path = root / "artifacts/real-paper-workflow-evidence.json"
                serialized = json.dumps(evidence, sort_keys=True, indent=2)
                evidence_path.write_text(serialized)
                evidence_path.chmod(0o600)
                result["evidence_checksum"] = hashlib.sha256(
                    serialized.encode()
                ).hexdigest()
                print(
                    "Real bound-context review completed; outcome "
                    + result["outcome"]
                    + ". Original report unchanged; no broker calls.",
                    flush=True,
                )
            else:
                print(
                    "Real review did not complete successfully. Its failure is retained; no retry made.",
                    flush=True,
                )
    except Exception:
        result["verification_failed"] = True
        raise
    finally:
        path = root / (
            "artifacts/real-paper-workflow-results.json"
            if result["fresh_analysis"]
            else "artifacts/real-governance-results.json"
        )
        path.write_text(json.dumps(result, indent=2))
        path.chmod(0o600)
        command(
            "--profile",
            "verification",
            "--profile",
            "governance_fixture",
            "down",
            "--volumes",
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- redact secrets at the verification boundary
        raise SystemExit(
            "Real-governance verification failed; sanitized results retained, no credentials printed."
        ) from None
