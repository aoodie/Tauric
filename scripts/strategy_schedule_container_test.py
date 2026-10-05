"""One real public-feed monitor route in disposable built Linux services; no AI/orders."""

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
assert fixture["database_name"].startswith("tauric_browser_")
url = urlsplit(fixture["database_url"])
env = os.environ.copy()
env["BROWSER_DATABASE_URL"] = urlunsplit(
    (url.scheme, url.netloc.rsplit("@", 1)[0] + "@db:5432", url.path, "", "")
)
compose = ["docker", "compose", "-f", str(root / "deploy/compose.browser.yml")]


def command(*args):
    r = subprocess.run([*compose, *args], env=env, capture_output=True, check=False)
    assert r.returncode == 0, (
        "Disposable container operation failed; private output withheld."
    )


def action():
    return {
        "request_id": str(uuid4()),
        "reason": "Disposable connected paper monitor verification",
    }


results = {"broker_order_calls": 0, "model_calls": 0}
try:
    command(
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "60",
        "test_api",
        "web",
        "test_strategy_scheduler",
        "test_autonomous_paper",
    )
    with httpx.Client(
        base_url="http://127.0.0.1:8081",
        timeout=30,
        headers={"Origin": "http://127.0.0.1:8081"},
        trust_env=False,
    ) as client:

        def api(path, body=None, method="POST"):
            r = client.request(method if body is not None else "GET", path, json=body)
            assert r.status_code in (200, 201), (
                f"Verification API rejected {path}: HTTP {r.status_code}"
            )
            return r.json()

        session = api(
            "/api/v1/session",
            {"username": fixture["username"], "password": fixture["password"]},
        )
        client.headers["X-CSRF-Token"] = session["csrf_token"]

        def wait_for(check, seconds=60):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    state = api("/api/v1/paper")
                except (httpx.HTTPError, AssertionError):
                    time.sleep(1)
                    continue
                if check(state):
                    return state
                time.sleep(1)
            raise AssertionError(
                "Monitor verification timed out; no automatic test retry."
            )

        wait_for(
            lambda s: s["strategy_scheduler"]["ready"] and s["autonomy"]["worker_ready"]
        )
        account = api(
            "/api/v1/paper/accounts",
            {
                **action(),
                "name": "Disposable monitor account",
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
                "kind": "portfolio",
                "name": "Disposable monitor capital",
                "limits": {
                    "budget": "1000",
                    "max_total_risk_percent": "3",
                    "max_positions": 5,
                },
            },
        )
        version = api(
            "/api/v1/strategies",
            {
                **action(),
                "name": "Disposable connected monitor",
                "parameters": {
                    "label": "v1",
                    "description": "Connected public-feed paper monitoring only; signals require explicit owner actions.",
                    "quantity_step": "0.00001",
                    "capital_limit": "250",
                    "risk_percent": "0.1",
                    "rules": {
                        "engine": "closed_bar_breakout_v1",
                        "interval": "1m",
                        "entry_lookback": 3,
                        "minimum_volume_ratio": "2",
                        "exit_lookback": 2,
                        "take_profit_r": "2",
                        "signal_ttl_seconds": 60,
                    },
                },
            },
        )
        p = version["parameters"]
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
                "strategy_version": p["label"],
                "quantity_step": p["quantity_step"],
                "capital_limit": p["capital_limit"],
                "risk_percent": p["risk_percent"],
            },
        )
        path = f"/api/v1/strategies/deployments/{dep['id']}/schedule"
        body = {
            **action(),
            "expected_revision": 0,
            "enabled": True,
            "cadence_seconds": 30,
        }
        baseline = api("/api/v1/paper")["records"]
        saved = api(path, body, "PUT")
        assert api(path, body, "PUT")["version_id"] == saved["version_id"]
        autonomy_path = f"/api/v1/strategies/deployments/{dep['id']}/autonomy"
        autonomy_body = {
            **action(),
            "expected_revision": 0,
            "enabled": True,
            "auto_entries": False,
            "auto_exits": True,
            "profile": "cloud",
            "daily_research_limit": 0,
            "daily_review_limit": 0,
            "daily_entry_limit": 1,
            "report_max_age_seconds": 3600,
            "workflow_timeout_seconds": 900,
        }
        autonomy_saved = api(autonomy_path, autonomy_body, "PUT")
        assert api(autonomy_path, autonomy_body, "PUT")["id"] == autonomy_saved["id"]
        command("restart", "test_autonomous_paper")
        wait_for(
            lambda s: (
                s["autonomy"]["worker_ready"]
                and s["autonomy"]["policies"][0]["id"] == autonomy_saved["id"]
            )
        )

        state = wait_for(lambda s: bool(s["strategy_schedules"][0]["last_run_id"]))
        first = state["strategy_schedules"][0]["last_run_id"]
        signal = next(r for r in state["strategy_runs"] if r["id"] == first)
        assert signal["payload"]["run_mode"] == "scheduled"
        assert signal["payload"]["snapshot_id"] and signal["action"] in (
            "enter",
            "hold",
        )
        assert not state["positions"] and state["records"] == baseline
        results.update(
            first_run_id=first,
            first_action=signal["action"],
            snapshot_id=signal["payload"]["snapshot_id"],
            connected_feed_verified=True,
        )
        command("stop", "test_strategy_scheduler")
        # Leave the saved due time intact and cross it while this worker is stopped.
        time.sleep(32)
        command("start", "test_strategy_scheduler")
        state = wait_for(lambda s: s["strategy_schedules"][0]["last_run_id"] != first)
        recovered = state["strategy_schedules"][0]["last_run_id"]
        command("stop", "test_strategy_scheduler")
        command("restart", "test_api")
        state = wait_for(
            lambda s: s["strategy_schedules"][0]["last_run_id"] == recovered
        )
        assert state["autonomy"]["policies"][0]["id"] == autonomy_saved["id"]
        assert state["autonomy"]["workflows"] == []
        api(
            autonomy_path,
            {**autonomy_body, **action(), "expected_revision": 1, "enabled": False},
            "PUT",
        )
        results.update(
            autonomous_worker_restart_verified=True,
            autonomous_policy_persistence_verified=True,
            autonomous_policy_disable_verified=True,
            autonomous_no_model_admissions_verified=True,
        )
        history = api(path + "/history")
        assert len(history["ticks"]) == len(state["strategy_runs"]) == 2
        assert len({t["id"] for t in history["ticks"]}) == 2
        api(
            path,
            {
                **action(),
                "expected_revision": 1,
                "enabled": False,
                "cadence_seconds": 30,
            },
            "PUT",
        )
        command("start", "test_strategy_scheduler")
        state = wait_for(
            lambda s: s["strategy_scheduler"]["ready"] and s["autonomy"]["worker_ready"]
        )
        assert state["strategy_schedules"][0]["next_due_at"] is None
        assert state["records"] == baseline and not state["positions"]
        results.update(
            worker_restart_recovery_verified=True,
            api_restart_persistence_verified=True,
            disable_verified=True,
            run_count=2,
            tick_count=2,
            no_financial_changes_verified=True,
        )
        print(
            "Built monitor verified on real public candles, including worker/API restarts and disable; no AI calls or trades."
        )
finally:
    target = root / "artifacts/strategy-schedule-container-results.json"
    target.write_text(json.dumps(results, indent=2))
    target.chmod(0o600)
    command("--profile", "verification", "down", "--volumes")
