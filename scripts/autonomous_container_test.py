"""Built Linux process recovery on a private DB; synthetic candles/AI, no model calls."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
source = Path(os.environ.get("TAURIC_VERIFICATION_SOURCE", root))
sys.path.insert(0, str(source / "backend"))
fixture_path = root / "artifacts/autonomy-container.json"
assert not fixture_path.exists(), (
    "Preserve or clean up an existing autonomy fixture first."
)
env = os.environ.copy()
env["TAURIC_BROWSER_FIXTURE"] = str(fixture_path)
result_path = root / "artifacts/autonomy-container-results.json"
results = {
    "synthetic_candles": True,
    "synthetic_native_and_governance_results": True,
    "model_calls": 0,
    "broker_calls": 0,
}
override = root / "artifacts/autonomy-compose.yml"
feed_path = root / "artifacts/autonomy-feed-state.json"
compose = [
    "docker",
    "compose",
    "-p",
    "tauric_autonomy_checks",
    "-f",
    str(root / "deploy/compose.browser.yml"),
    "-f",
    str(override),
]
started = False


def command(args):
    result = subprocess.run(args, env=env, capture_output=True, check=False)
    if result.returncode:
        diagnostics = root / "artifacts/autonomy-container-private.log"
        logs = (
            subprocess.run(
                [*compose, "logs", "--no-color", "--tail", "60"],
                env=env,
                capture_output=True,
                check=False,
            )
            if override.exists()
            else None
        )
        diagnostics.write_bytes(
            result.stdout + result.stderr + (logs.stdout + logs.stderr if logs else b"")
        )
        diagnostics.chmod(0o600)
        raise AssertionError(
            "Disposable verification command failed; private diagnostics saved locally."
        )
    return result.stdout


def action(reason="Disposable autonomous process verification"):
    return {"request_id": str(uuid4()), "reason": reason}


try:
    command([sys.executable, str(source / "scripts/prepare_browser_test.py")])
    fixture = json.loads(fixture_path.read_text())
    assert fixture["database_name"].startswith("tauric_browser_")
    os.environ["TAURIC_DATABASE_URL"] = fixture["database_url"]
    os.environ["TAURIC_MODEL_KEY_FILE"] = str(fixture_path.with_suffix(".key"))
    os.environ["TAURIC_OPENCLAW_PRIVATE_FILE"] = str(
        root / ".env.openclaw-private.json"
    )
    from tauric import analysis, autonomous, governance
    from tauric.config import runtime_config
    from tauric.database import engine
    from tauric.models import AnalysisJob, GatewayStatus, GovernanceJob, Owner

    runtime_config.cache_clear()
    engine.cache_clear()
    url = urlsplit(fixture["database_url"])
    env["BROWSER_DATABASE_URL"] = urlunsplit(
        (url.scheme, url.netloc.rsplit("@", 1)[0] + "@db:5432", url.path, "", "")
    )
    key = str(fixture_path.with_suffix(".key"))
    private = str(root / ".env.openclaw-private.json")
    fixture_script = str(root / "scripts/autonomous_worker_fixture.py")
    image = os.environ.get("TAURIC_VERIFICATION_IMAGE", "tauric_foundation-api")
    web_image = os.environ.get("TAURIC_VERIFICATION_WEB_IMAGE", "tauric_foundation-web")
    override.write_text(f"""services:
  web:
    image: {web_image}
  test_api:
    image: {image}
    volumes:
      - {key}:/run/setup-model-key:ro
  test_strategy_scheduler:
    image: {image}
    environment:
      PYTHONPATH: /app
    volumes:
      - {fixture_script}:/fixtures/autonomous_worker_fixture.py:ro
      - {feed_path}:/fixtures/autonomy-feed-state.json:ro
    command: ["python", "/fixtures/autonomous_worker_fixture.py", "monitor"]
  test_autonomous_paper:
    image: {image}
    environment:
      PYTHONPATH: /app
    volumes:
      - {key}:/run/setup-model-key:ro
      - {private}:/run/setup-openclaw-private.json:ro
      - {fixture_script}:/fixtures/autonomous_worker_fixture.py:ro
      - {feed_path}:/fixtures/autonomy-feed-state.json:ro
    command: ["python", "/fixtures/autonomous_worker_fixture.py", "execute"]
""")
    override.chmod(0o600)
    feed_path.write_text(json.dumps({"mode": "enter"}))
    command(
        [
            *compose,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "60",
            "test_api",
            "web",
            "test_strategy_scheduler",
            "test_autonomous_paper",
        ]
    )
    started = True
    with httpx.Client(
        base_url="http://127.0.0.1:8081",
        headers={"Origin": "http://127.0.0.1:8081"},
        timeout=20,
        trust_env=False,
    ) as client:

        def api(path, body=None, method="POST", extra_headers=None):
            response = client.request(
                method if body is not None else "GET",
                path,
                json=body,
                headers=extra_headers,
            )
            assert response.status_code in (200, 201), (
                f"Verification API rejected {path}: HTTP {response.status_code}"
            )
            return response.json()

        session = api(
            "/api/v1/session",
            {"username": fixture["username"], "password": fixture["password"]},
        )
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        api(
            "/api/v1/model-connections/cloud",
            {
                "expected_revision": 0,
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9/v1",
                "quick_model": "isolated-fixture",
                "deep_model": "isolated-fixture",
                "key_action": "replace",
                "api_key": "not-a-real-model-key",
            },
            "PUT",
        )

        def heartbeats():
            with Session(engine()) as db:
                owner = db.scalar(select(Owner))
                analysis.heartbeat(db, "explicit-native-fixture")
                routing_fingerprint = governance.routing(db, owner)["fingerprint"]
                gateway = db.get(GatewayStatus, "openclaw")
                if not gateway:
                    gateway = GatewayStatus(id="openclaw")
                    db.add(gateway)
                gateway.fingerprint = routing_fingerprint
                gateway.heartbeat_at = autonomous.now()
                governance.heartbeat(db)

        def wait_for(check, seconds=30):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                heartbeats()
                try:
                    state = api("/api/v1/paper")
                except (httpx.HTTPError, AssertionError):
                    time.sleep(0.5)
                    continue
                if check(state):
                    return state
                time.sleep(0.5)
            raise AssertionError(
                "Autonomous process verification timed out; no model retry was made."
            )

        wait_for(
            lambda s: s["autonomy"]["worker_ready"] and s["strategy_scheduler"]["ready"]
        )
        account = api(
            "/api/v1/paper/accounts",
            {
                **action(),
                "name": "Disposable autonomous account",
                "currency": "USDT",
                "starting_balance": "10000",
                "policy": {
                    "risk_percent": "1",
                    "max_total_risk_percent": "3",
                    "max_exposure_percent": "80",
                    "max_positions": 5,
                    "fee_bps": "10",
                    "slippage_bps": "5",
                    "quote_max_age_seconds": 60,
                    "decision_ttl_seconds": 300,
                },
            },
        )
        scope = api(
            "/api/v1/paper/capital",
            {
                **action(),
                "account_id": account["id"],
                "parent_id": None,
                "name": "Disposable allocation",
                "kind": "portfolio",
                "limits": {
                    "budget": "5000",
                    "max_total_risk_percent": "3",
                    "max_positions": 5,
                },
            },
        )
        parameters = {
            "label": "v1",
            "description": "Synthetic process verification only",
            "quantity_step": "0.0001",
            "capital_limit": "5000",
            "risk_percent": "1",
            "rules": {
                "engine": "closed_bar_breakout_v1",
                "interval": "1m",
                "entry_lookback": 3,
                "minimum_volume_ratio": "2",
                "exit_lookback": 2,
                "take_profit_r": "2",
                "signal_ttl_seconds": 60,
            },
        }
        version = api(
            "/api/v1/strategies",
            {
                **action(),
                "name": "Disposable autonomous breakout",
                "parameters": parameters,
            },
        )
        dep = api(
            "/api/v1/paper/deployments",
            {
                **action(),
                "account_id": account["id"],
                "instrument_id": "binance:spot:BTCUSDT",
                "portfolio": scope["name"],
                "capital_pot": scope["name"],
                "capital_scope_id": scope["id"],
                "strategy_revision_id": version["id"],
                "strategy": version["name"],
                "strategy_version": "v1",
                "quantity_step": "0.0001",
                "capital_limit": "5000",
                "risk_percent": "1",
            },
        )
        # Start just after a real candle boundary so restart + fixture stages fit its TTL.
        while datetime.now(UTC).second not in range(2, 9):
            heartbeats()
            time.sleep(0.5)
        schedule_path = f"/api/v1/strategies/deployments/{dep['id']}/schedule"
        api(
            schedule_path,
            {
                **action(),
                "expected_revision": 0,
                "enabled": True,
                "cadence_seconds": 30,
            },
            "PUT",
        )
        policy_path = f"/api/v1/strategies/deployments/{dep['id']}/autonomy"
        pol = {
            **action(),
            "expected_revision": 0,
            "enabled": True,
            "auto_entries": True,
            "auto_exits": True,
            "profile": "cloud",
            "daily_research_limit": 1,
            "daily_review_limit": 2,
            "daily_entry_limit": 1,
            "report_max_age_seconds": 3600,
            "workflow_timeout_seconds": 300,
        }
        api(policy_path, pol, "PUT")
        state = wait_for(
            lambda s: (
                bool(s["autonomy"]["workflows"])
                and s["autonomy"]["workflows"][0]["analysis_job_id"] is not None
            )
        )
        workflow = state["autonomy"]["workflows"][0]
        assert workflow["status"] == "waiting_research"
        command([*compose, "restart", "test_autonomous_paper"])
        state = wait_for(
            lambda s: (
                s["autonomy"]["worker_ready"]
                and s["autonomy"]["workflows"][0]["id"] == workflow["id"]
            )
        )
        assert state["autonomy"]["policies"][0]["usage_today"]["research"] == 1
        with Session(engine()) as db:
            job_id = analysis.claim(db)
            assert job_id == workflow["analysis_job_id"]
            job = db.get(AnalysisJob, job_id)
            original = {
                "request": job.request,
                "reports": {
                    k: "Explicit synthetic native BUY fixture for process recovery; no real market recommendation."
                    for k in autonomous.REPORTS
                },
                "native_rating": "BUY",
                "execution_authority": False,
                "governance": None,
            }
            assert analysis.finish(db, job_id, original)
        state = wait_for(
            lambda s: s["autonomy"]["workflows"][0]["status"] == "waiting_review"
        )
        workflow = state["autonomy"]["workflows"][0]
        with Session(engine()) as db:
            claim = governance.claim(db)
            job = db.get(GovernanceJob, workflow["review_job_id"])
            assert claim[0] == job.id
            checksum, proposal_id, policy_id = (
                job.checksum,
                job.proposal_id,
                job.context["policy_id"],
            )
        # The public proxy deliberately blocks /internal. Retrieve bound context
        # through the real private API inside its container, without exporting a token.
        context = json.loads(
            command(
                [
                    *compose,
                    "exec",
                    "-T",
                    "test_api",
                    "python",
                    "-c",
                    (
                        "import json,urllib.request; "
                        "token=json.load(open('/run/setup-openclaw-private.json'))['platform_token']; "
                        f"req=urllib.request.Request('http://127.0.0.1:8000/internal/openclaw/jobs/{claim[0]}/context', headers={{'Authorization':'Bearer '+token}}); "
                        "print(urllib.request.urlopen(req,timeout=10).read().decode())"
                    ),
                ]
            )
        )
        assert context["context_checksum"] == checksum
        with Session(engine()) as db:
            assert governance.finish(
                db,
                *claim,
                raw=json.dumps(
                    {
                        "job_id": claim[0],
                        "context_checksum": checksum,
                        "proposal_id": proposal_id,
                        "policy_id": policy_id,
                        "outcome": "allow",
                        "reason": "Explicit synthetic bound review fixture; paper mechanics only.",
                    }
                ),
            )
        state = wait_for(lambda s: s["autonomy"]["workflows"][0]["status"] == "opened")
        entry_workflow = state["autonomy"]["workflows"][0]
        entry = next(
            r for r in state["records"] if r["id"] == entry_workflow["entry_fill_id"]
        )
        assert len(state["positions"]) == 1
        command([*compose, "restart", "test_api", "test_autonomous_paper"])
        state = wait_for(
            lambda s: (
                len(s["positions"]) == 1
                and s["autonomy"]["workflows"][0]["entry_fill_id"] == entry["id"]
            )
        )
        feed_path.write_text(json.dumps({"mode": "stop"}))
        # A complete post-entry minute must pass. The built monitor/worker own closing.
        state = wait_for(
            lambda s: not s["positions"] and len(s["autonomy"]["workflows"]) == 2,
            seconds=160,
        )
        state = wait_for(
            lambda s: all(w["status"] == "closed" for w in s["autonomy"]["workflows"]),
            seconds=35,
        )
        fills = [r for r in state["records"] if r["kind"] == "fill"]
        assert len(fills) == 2
        closed = next(r for r in fills if r["payload"]["side"] == "sell")
        assert (
            closed["payload"]["position_id"] == entry["id"]
            and closed["payload"]["exit_source"] == "autonomous_rule_close"
        )
        pnl = (
            Decimal(entry["payload"]["quantity"])
            * (Decimal(closed["payload"]["price"]) - Decimal(entry["payload"]["price"]))
            - Decimal(entry["payload"]["fee"])
            - Decimal(closed["payload"]["fee"])
        )
        assert Decimal(state["accounts"][0]["cash"]) == Decimal(10000) + pnl
        assert Decimal(state["accounts"][0]["realized_pnl"]) == pnl
        api(
            policy_path,
            {**pol, **action(), "expected_revision": 1, "enabled": False},
            "PUT",
        )
        command([*compose, "restart", "test_autonomous_paper"])
        state = wait_for(
            lambda s: (
                s["autonomy"]["worker_ready"]
                and not s["autonomy"]["policies"][0]["settings"]["enabled"]
            )
        )
        assert len([r for r in state["records"] if r["kind"] == "fill"]) == 2
        results.update(
            waiting_research_restart=True,
            open_position_and_api_restart=True,
            automatic_attributed_exit=True,
            closed_workflow_reconciliation=True,
            disabled_restart=True,
            duplicate_fill_count=0,
            entry_fill_id=entry["id"],
            close_fill_id=closed["id"],
            realized_pnl=str(pnl),
        )
        with Session(engine()) as db:
            from tauric.models import AnalysisArtifact

            assert (
                db.get(AnalysisArtifact, entry_workflow["native_artifact_id"]).payload
                == original
            )
        results.update(original_report_preserved=True, passed=True)
        print(
            "Built autonomous paper workflow passed process/API restarts, one entry/exit and independent journal reconciliation. Candles and AI outcomes were explicit fixtures; no model or broker calls."
        )
finally:
    if started or override.exists():
        command(
            [
                *compose,
                "--profile",
                "verification",
                "--profile",
                "governance_fixture",
                "down",
                "--volumes",
            ]
        )
    if fixture_path.exists():
        command([sys.executable, str(source / "scripts/cleanup_browser_test.py")])
    for target in (override, feed_path):
        target.unlink(missing_ok=True)
    result_path.write_text(json.dumps(results, indent=2) + "\n")
    result_path.chmod(0o600)
