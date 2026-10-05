"""Built private Gateway + real SDK + fake provider. No real model or broker calls."""

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))

fixture = json.loads((root / "artifacts/browser-test.json").read_text())
url = urlsplit(fixture["database_url"])
environment = os.environ.copy()
environment["BROWSER_DATABASE_URL"] = urlunsplit(
    (
        url.scheme,
        url.netloc.rsplit("@", 1)[0] + "@db:5432",
        url.path,
        url.query,
        url.fragment,
    )
)
compose = ["docker", "compose", "-f", str(root / "deploy/compose.browser.yml")]
results = []


def command(*args):
    result = subprocess.run(
        [*compose, *args], env=environment, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            "Isolated governance container command failed; no credentials printed."
        )
    return result.stdout


def api(page, path, body=None, method="POST"):
    response = page.evaluate(
        """async ({path,body,method})=>{
       const s=await (await fetch('/api/v1/session')).json();
       const r=await fetch(path,{method:body?method:'GET',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:body?JSON.stringify(body):undefined});
       return {status:r.status, data:await r.json()};
    }""",
        {"path": path, "body": body, "method": method},
    )
    assert response["status"] in (200, 201), response["data"]
    return response["data"]


def wait_ready(page):
    for _ in range(100):
        status = api(page, "/api/v1/governance")
        if all(r["ready"] for r in status["routes"]):
            return
        page.wait_for_timeout(1000)
    raise RuntimeError("Private Gateway did not apply the fixture settings.")


def wait_job(page, proposal_id):
    for _ in range(100):
        status = api(page, "/api/v1/governance")
        jobs = [j for j in status["jobs"] if j["proposal_id"] == proposal_id]
        if jobs and jobs[0]["status"] not in ("queued", "running"):
            return jobs[0]
        page.wait_for_timeout(1000)
    raise RuntimeError("Fixture review did not complete within its test budget.")


def action(reason="Isolated governance transport fixture"):
    return {"request_id": str(uuid4()), "reason": reason}


try:
    command(
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "120",
        "test_api",
        "web",
        "fixture_model",
        "test_openclaw",
        "test_governance_worker",
    )
    # Original research is deliberately fabricated test evidence, never production data.
    original = {
        "native_rating": "HOLD",
        "reports": {
            "final_trade_decision": "ISOLATED NATIVE FIXTURE. Daily USD evidence only; do not interpret as investment research."
        },
        "governance": None,
        "execution_authority": False,
        "fixture": True,
    }
    seeded = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            str(root / "scripts/governance_fixture_seed.py"),
        ],
        input=json.dumps(original).encode(),
        capture_output=True,
        check=False,
    )
    if seeded.returncode:
        raise RuntimeError("Disposable native fixture could not be created.")
    artifact_id = seeded.stdout.decode().strip()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 1000})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto("http://127.0.0.1:8081")
        page.get_by_label("Username", exact=True).fill(fixture["username"])
        page.get_by_label("Password", exact=True).fill(fixture["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("button", name="Settings", exact=True).click()
        page.get_by_role("tab", name="AI connections", exact=True).click()
        for label, model in [
            ("Direct provider API", "fixture-cloud"),
            ("9router / compatible API", "fixture-local"),
        ]:
            form = page.get_by_role("form", name=label + " settings", exact=True)
            form.get_by_label("Provider", exact=True).select_option("openai_compatible")
            form.get_by_label("API address", exact=True).fill(
                "http://fixture_model:9000/v1"
            )
            form.get_by_label("Quick model", exact=True).fill(model + "-quick")
            form.get_by_label("Deep model", exact=True).fill(model + "-deep")
            form.get_by_label("API key action", exact=True).select_option("replace")
            form.get_by_label("New API key", exact=True).fill(
                "fixture-key-not-a-real-credential"
            )
            form.get_by_role("button", name="Save connection", exact=True).click()
            expect(form.get_by_role("status")).to_contain_text("Connection saved")
        expect(
            page.get_by_role("heading", name="OpenClaw review settings", exact=True)
        ).to_be_visible()
        wait_ready(page)
        page.screenshot(
            path=str(root / "artifacts/governance-settings.png"), full_page=True
        )
        results.append(
            "Both saved Settings connections automatically applied to the actual pinned Gateway"
        )
        pol = {
            "risk_percent": "1",
            "max_total_risk_percent": "3",
            "max_exposure_percent": "80",
            "max_positions": 5,
            "fee_bps": "10",
            "slippage_bps": "5",
            "quote_max_age_seconds": 60,
            "decision_ttl_seconds": 300,
        }
        acct = api(
            page,
            "/api/v1/paper/accounts",
            {
                **action(),
                "name": "Governance fixture account",
                "currency": "USDT",
                "starting_balance": "10000",
                "policy": pol,
            },
        )
        dep = api(
            page,
            "/api/v1/paper/deployments",
            {
                **action(),
                "account_id": acct["id"],
                "instrument_id": "binance:spot:BTCUSDT",
                "portfolio": "Governance fixture portfolio",
                "capital_pot": "Governance fixture capital",
                "strategy": "Governance fixture strategy",
                "strategy_version": "v1",
                "quantity_step": "0.00001",
                "capital_limit": "10000",
                "risk_percent": "1",
            },
        )
        for route in ["cloud", "local"]:
            q = api(
                page, "/api/v1/market/binance:spot:BTCUSDT/refresh", {"interval": "1m"}
            )
            stop = str(
                (Decimal(q["candles"][-1]["close"]) * Decimal("0.9")).quantize(
                    Decimal("0.01")
                )
            )
            prop = api(
                page,
                "/api/v1/paper/proposals",
                {
                    **action(),
                    "deployment_id": dep["id"],
                    "stop_price": stop,
                    "native_artifact_id": artifact_id,
                },
            )
            page.get_by_role("button", name="Paper trading", exact=True).click()
            # The page polls stored records. Reselect after the completed API write.
            page.wait_for_timeout(5500)
            page.get_by_label("Recorded proposal", exact=True).select_option(prop["id"])
            page.get_by_label("Reviewer", exact=True).select_option(route)
            request_button = page.get_by_role(
                "button", name="Request OpenClaw review", exact=True
            )
            expect(request_button).to_be_enabled(timeout=10000)
            request_button.click()
            done = wait_job(page, prop["id"])
            assert done["status"] == "succeeded", done["error"]
            assert done["usage"]["total_tokens"] == 80
            expect(
                page.get_by_text("ALLOW · OPENCLAW REVIEW", exact=True)
            ).to_be_visible(timeout=10000)
            inspect = api(
                page, f"/api/v1/resources/trading_record/{done['decision_id']}"
            )["data"]
            assert (
                inspect["payload"]["source"] == "openclaw"
                and inspect["payload"]["model"]["model"] == f"fixture-{route}-deep"
            )
            assert inspect["payload"]["context_checksum"] == done["context_checksum"]
            page.get_by_role("button", name="Build paper plan", exact=True).click()
            expect(
                page.get_by_role("button", name="Execute paper plan", exact=True)
            ).to_be_visible(timeout=10000)
            page.get_by_role("button", name="Execute paper plan", exact=True).click()
            expect(
                page.get_by_text(
                    "Simulated fill recorded in the paper ledger.", exact=True
                )
            ).to_be_visible(timeout=10000)
            state = api(page, "/api/v1/paper")
            positions = [v for v in state["positions"] if v["account_id"] == acct["id"]]
            assert len(positions) == 1
            api(page, f"/api/v1/paper/positions/{positions[0]['id']}/close", action())
            results.append(
                f"{route}: actual Gateway tool call, validated governance, Decimal plan, paper fill and attributed close"
            )
            page.screenshot(
                path=str(root / f"artifacts/governance-{route}.png"), full_page=True
            )
        assert (
            api(page, f"/api/v1/analysis-artifacts/{artifact_id}")["payload"]
            == original
        )
        results.append(
            "Original native fixture remained unchanged and all decisions stayed paper-only"
        )
        # Change models in Settings, then test a real malformed final response through the Gateway.
        page.get_by_role("button", name="Settings", exact=True).click()
        page.get_by_role("tab", name="AI connections", exact=True).click()
        form = page.get_by_role("form", name="Direct provider API settings", exact=True)
        form.get_by_label("Deep model", exact=True).fill("fixture-malformed")
        form.get_by_label("API key action", exact=True).select_option("replace")
        form.get_by_label("New API key", exact=True).fill("fixture-rotated-key")
        form.get_by_role("button", name="Save connection", exact=True).click()
        expect(form.get_by_role("status")).to_contain_text("Connection saved")
        wait_ready(page)
        api(page, "/api/v1/market/binance:spot:BTCUSDT/refresh", {"interval": "1m"})
        prop = api(
            page,
            "/api/v1/paper/proposals",
            {
                **action(),
                "deployment_id": dep["id"],
                "stop_price": stop,
                "native_artifact_id": artifact_id,
            },
        )
        request = {**action(), "proposal_id": prop["id"], "profile": "cloud"}
        job = api(page, "/api/v1/governance/jobs", request)
        assert api(page, "/api/v1/governance/jobs", request)["id"] == job["id"]
        done = wait_job(page, prop["id"])
        assert done["status"] == "failed" and done["decision_id"] is None
        assert done["usage"]["total_tokens"] == 80
        results.append(
            "Malformed actual Gateway response fails closed; idempotent request does not duplicate work"
        )
        assert (
            page.request.get(
                "http://127.0.0.1:8081/internal/openclaw/runtime-config"
            ).status
            == 404
        )
        public = api(page, "/api/v1/governance")
        assert "fixture-key-not-a-real-credential" not in json.dumps(public)
        page.get_by_role("button", name="Paper trading", exact=True).click()
        page.wait_for_timeout(5500)
        page.get_by_label("Recorded proposal", exact=True).select_option(prop["id"])
        page.get_by_label("Reviewer", exact=True).select_option("cloud")
        expect(page.get_by_text("FAILED · CLOUD", exact=True)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=str(root / "artifacts/governance-mobile.png"), full_page=True
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert not errors, errors
        results.append(
            "Public/private separation, visible failure and usable mobile interface"
        )
        browser.close()
    metrics = json.loads(
        command(
            "exec",
            "-T",
            "test_api",
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://fixture_model:9000/metrics').read().decode())",
        )
    )
    assert len(metrics["calls"]) == 6, metrics
    assert all(m["tools"] == ["tauric_review_context"] for m in metrics["calls"])
    assert sum(m["tool_response"] for m in metrics["calls"]) == 3
    (root / "artifacts/governance-browser-results.json").write_text(
        json.dumps(
            {
                "passed": results,
                "fixture_model_calls": len(metrics["calls"]),
                "real_model_calls": 0,
                "broker_order_calls": 0,
            },
            indent=2,
        )
    )
    print(
        f"{len(results)} built governance workflows passed; 6 isolated fixture model requests, zero real model/broker calls."
    )
finally:
    command(
        "--profile",
        "verification",
        "--profile",
        "governance_fixture",
        "down",
        "--volumes",
    )
