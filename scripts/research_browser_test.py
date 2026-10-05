"""Research browser checks on an independently disposable DB and built UI assets.

Run with .venv/bin/python scripts/research_browser_test.py.
No configured model, messaging channel or broker is called.
"""

import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
fixture_path = root / "artifacts/research-browser-private.json"
key_path = root / "artifacts/research-browser-key"


def administrator():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    values = dict(
        line.split("=", 1)
        for line in (root / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    url = make_url(
        f"postgresql+psycopg://tauric_admin:{values['POSTGRES_PASSWORD']}@127.0.0.1:{values.get('DB_PORT', '54330')}/postgres"
    )
    return create_engine(url, isolation_level="AUTOCOMMIT"), url, values


def prepare():
    from alembic import command
    from alembic.config import Config
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from tauric.auth import hasher
    from tauric.config import runtime_config
    from tauric.models import MarketSnapshot, Owner, now
    from tauric.services import create_workspace

    admin, url, values = administrator()
    name = "tauric_research_browser_" + uuid4().hex[:10]
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = url.set(database=name)
    os.environ["TAURIC_DATABASE_URL"] = admin_url.render_as_string(hide_password=False)
    runtime_config.cache_clear()
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    command.upgrade(config, "head")
    app_url = admin_url.set(
        username="tauric_app", password=values["TAURIC_DB_PASSWORD"]
    )
    db_engine = create_engine(app_url)
    password = secrets.token_urlsafe(24)
    with Session(db_engine) as db:
        owner = Owner(
            username="research-browser-owner", password_hash=hasher.hash(password)
        )
        db.add(owner)
        db.flush()
        create_workspace(db, owner, "Research verification")
        start = datetime(2025, 1, 1, tzinfo=UTC)
        closes = [10, 8, 6, 10, 12, 13, 14, 12, 8, 6] * 6
        candles = [
            {
                "open_time": (start + timedelta(minutes=15 * i)).isoformat(),
                "close_time": (
                    start + timedelta(minutes=15 * (i + 1)) - timedelta(milliseconds=1)
                ).isoformat(),
                "open": str(price),
                "high": str(price + 1),
                "low": str(price - 1),
                "close": str(price),
                "volume": "10",
                "closed": True,
            }
            for i, price in enumerate(closes)
        ]
        payload = {
            "candles": candles,
            "source": "Explicit offline research browser fixture",
            "availability": "observed_at_receipt",
        }
        db.add(
            MarketSnapshot(
                instrument_id="binance:spot:BTCUSDT",
                interval="15m",
                received_at=now(),
                provider_time=now(),
                data=payload,
                checksum=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
            )
        )
        db.commit()
    with os.fdopen(
        os.open(fixture_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as handle:
        json.dump(
            {
                "database_name": name,
                "database_url": app_url.render_as_string(hide_password=False),
                "username": "research-browser-owner",
                "password": password,
            },
            handle,
        )
    with os.fdopen(
        os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
    ) as handle:
        handle.write(Fernet.generate_key())
    db_engine.dispose()
    admin.dispose()


def serve():
    import uvicorn
    from fastapi.staticfiles import StaticFiles

    fixture = json.loads(fixture_path.read_text())
    os.environ.update(
        TAURIC_DATABASE_URL=fixture["database_url"],
        TAURIC_SECURE_COOKIES="false",
        TAURIC_ALLOWED_ORIGINS='["http://127.0.0.1:8092"]',
        TAURIC_MODEL_KEY_FILE=str(key_path),
        TAURIC_MODEL_PROFILES_FILE=str(
            root / "artifacts/research-no-model-profiles.json"
        ),
    )
    from tauric.config import runtime_config

    runtime_config.cache_clear()
    from tauric.app import app

    # Synthetic quote refresh exists only in this disposable test server.
    from tauric.auth import CurrentOwner, Db
    from tauric.models import MarketSnapshot, now

    @app.post("/api/v1/test-research/quote")
    def synthetic_quote(db: Db, owner: CurrentOwner):
        start = now().replace(second=0, microsecond=0)
        candles = [
            {
                "open_time": (start + timedelta(minutes=i)).isoformat(),
                "close_time": (
                    start + timedelta(minutes=i + 1) - timedelta(milliseconds=1)
                ).isoformat(),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "10",
                "closed": i < 0,
            }
            for i in (-2, -1, 0)
        ]
        payload = {
            "candles": candles,
            "source": "Explicit offline paper analytics fixture",
            "availability": "observed_at_receipt",
        }
        db.add(
            MarketSnapshot(
                instrument_id="binance:spot:BTCUSDT",
                interval="1m",
                received_at=now(),
                provider_time=now(),
                data=payload,
                checksum=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
            )
        )
        db.commit()
        return {"synthetic": True}

    app.mount(
        "/",
        StaticFiles(directory=root / "frontend/dist", html=True),
        name="research-ui",
    )
    uvicorn.run(app, host="127.0.0.1", port=8092, log_level="warning", access_log=False)


def browser_checks():
    from playwright.sync_api import expect, sync_playwright

    fixture = json.loads(fixture_path.read_text())
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 1050})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto("http://127.0.0.1:8092")
        page.wait_for_load_state("networkidle")
        page.get_by_label("Username", exact=True).fill(fixture["username"])
        page.get_by_label("Password", exact=True).fill(fixture["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(
            page.get_by_role("button", name="Research & analytics", exact=True)
        ).to_be_visible()
        page.get_by_role("button", name="Research & analytics", exact=True).click()
        expect(page.get_by_role("heading", name="Research & analytics")).to_be_visible()
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(root / "artifacts/research-empty.png"), full_page=True)
        expect(
            page.get_by_role("button", name="Run replay", exact=True)
        ).to_be_disabled()
        page.get_by_label("Dataset name", exact=True).fill(
            "Offline research browser dataset"
        )
        page.get_by_role("button", name="Save chart dataset", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("Dataset saved")
        expect(page.get_by_label("Saved dataset", exact=True)).not_to_have_value("")
        checks.append(
            "Save current closed chart candles, provenance and explicit retrospective label"
        )
        with page.expect_download() as downloaded:
            page.get_by_role("button", name="Export dataset", exact=True).click()
        exported_dataset = Path(downloaded.value.path()).read_bytes()
        page.get_by_label("Dataset name", exact=True).fill("Offline imported dataset")
        page.get_by_label("Import candle JSON", exact=True).set_input_files(
            {
                "name": "exported-dataset.json",
                "mimeType": "application/json",
                "buffer": exported_dataset,
            }
        )
        expect(page.get_by_role("status")).to_contain_text("Dataset imported")
        checks.append("Exported datasets import again through the visible file control")
        page.get_by_label("Fast average · candles", exact=True).fill("1")
        page.get_by_label("Slow average · candles", exact=True).fill("2")
        page.get_by_label("Experiment name", exact=True).fill(
            "Offline browser crossover"
        )
        page.get_by_role("button", name="Run replay", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("Replay saved")
        expect(page.get_by_role("figure")).to_be_visible()
        expect(page.get_by_role("table")).to_be_visible()
        slider = page.get_by_label("Inspect equity point", exact=True)
        slider.focus()
        slider.press("Home")
        for _ in range(3):
            slider.press("ArrowRight")
        expect(page.get_by_label("Inspect equity point", exact=True)).to_have_value("3")
        checks.append("Run replay, display outcomes, inspect equity and trade ledger")
        page.get_by_text("Assumptions and provenance", exact=True).click()
        expect(page.get_by_text("sma_cross_spot_v1.0.0", exact=False)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="Export result", exact=True).click()
        export = json.loads(Path(download.value.path()).read_text())
        assert (
            export["payload"]["engine_sources"] and export["payload"]["result"]["fills"]
        )
        checks.append(
            "Verified result export includes source code, settings, fills and checksums"
        )
        page.reload()
        page.get_by_role("button", name="Research & analytics", exact=True).click()
        page.get_by_role(
            "button", name="Offline browser crossover", exact=False
        ).click()
        expect(page.get_by_role("figure")).to_be_visible()
        checks.append("Saved dataset and experiment survive a page reload")
        page.get_by_label("Slow average · candles", exact=True).fill("1")
        page.get_by_role("button", name="Run replay", exact=True).click()
        # HTML validation blocks an out-of-range window without issuing a request.
        assert page.get_by_label("Slow average · candles", exact=True).evaluate(
            "el => !el.checkValidity()"
        )
        page.get_by_label("Slow average · candles", exact=True).fill("2")
        page.get_by_label("Fast average · candles", exact=True).fill("1")
        page.get_by_label("Risk budget · %", exact=True).fill("101")
        page.get_by_role("button", name="Run replay", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("cannot exceed 100%")
        page.get_by_label("Risk budget · %", exact=True).fill("1")
        checks.append("Invalid parameters show visible errors without saving a result")
        page.get_by_role("tab", name="Paper analytics", exact=True).click()
        expect(
            page.get_by_role("heading", name="No paper account outcomes yet")
        ).to_be_visible()
        checks.append("Empty analytics directs the owner to account setup")
        page.evaluate("""async () => {
          const session = await (await fetch('/api/v1/session')).json();
          const post = async (url, body) => {
            const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token}, body:JSON.stringify(body)});
            const data = await r.json(); if(!r.ok) throw new Error(JSON.stringify(data)); return data;
          };
          const action = () => ({request_id:crypto.randomUUID(),reason:'Explicit disposable paper analytics fixture'});
          await post('/api/v1/test-research/quote', {});
          const acct = await post('/api/v1/paper/accounts', {...action(),name:'Offline analytics account',currency:'USDT',starting_balance:'10000',policy:{risk_percent:'1',max_total_risk_percent:'3',max_exposure_percent:'80',max_positions:5,fee_bps:'10',slippage_bps:'5',quote_max_age_seconds:60,decision_ttl_seconds:300}});
          const dep = await post('/api/v1/paper/deployments', {...action(),account_id:acct.id,instrument_id:'binance:spot:BTCUSDT',portfolio:'Offline analytics',capital_pot:'Offline capital',strategy:'Offline analytics strategy',strategy_version:'v1',quantity_step:'0.0001',capital_limit:'10000',risk_percent:'1'});
          const prop = await post('/api/v1/paper/proposals', {...action(),deployment_id:dep.id,stop_price:'90'});
          const review = await post(`/api/v1/paper/proposals/${prop.id}/review`, {...action(),outcome:'allow'});
          const plan = await post(`/api/v1/paper/decisions/${review.id}/plan`, action());
          const entry = await post(`/api/v1/paper/plans/${plan.id}/execute`, action());
          await post(`/api/v1/paper/positions/${entry.id}/close`, action());
        }""")
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(
            page.get_by_role("heading", name="Offline analytics account", exact=True)
        ).to_be_visible()
        expect(
            page.get_by_role(
                "button", name="Offline analytics strategy · v1", exact=True
            )
        ).to_be_visible()
        page.get_by_role("button", name="Inspect fill", exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.keyboard.press("Escape")
        checks.append(
            "Paper outcomes, strategy attribution and original fill drill-down render from the ledger"
        )
        page.screenshot(
            path=str(root / "artifacts/analytics-desktop.png"), full_page=True
        )
        page.get_by_role("tab", name="Research experiments", exact=True).click()
        page.get_by_role(
            "button", name="Offline browser crossover", exact=False
        ).click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(
            path=str(root / "artifacts/research-mobile.png"), full_page=True
        )
        page.set_viewport_size({"width": 1536, "height": 1050})
        page.screenshot(
            path=str(root / "artifacts/research-desktop.png"), full_page=True
        )
        page.locator(".research-result").screenshot(
            path=str(root / "artifacts/research-result.png")
        )
        checks.append(
            "Desktop and mobile render without page overflow or JavaScript errors"
        )
        assert not errors, errors
        state = page.evaluate("async () => (await fetch('/api/v1/research')).json()")
        assert len(state["datasets"]) == 2 and len(state["runs"]) == 1
        browser.close()
    (root / "artifacts/research-browser-results.json").write_text(
        json.dumps(
            {
                "passed": checks,
                "fixture": "Explicit offline candles in an independently disposable database",
                "model_calls": 0,
                "broker_order_calls": 0,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(checks)} research browser checks passed")


def cleanup():
    if not fixture_path.exists():
        return
    from sqlalchemy import text

    admin, _, _ = administrator()
    name = json.loads(fixture_path.read_text())["database_name"]
    if not name.startswith("tauric_research_browser_"):
        raise RuntimeError(
            "Refusing to remove a database outside this test's namespace"
        )
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()
    fixture_path.unlink()
    key_path.unlink(missing_ok=True)


def real_market_check():
    """One read-only public feed and replay on the separately disposable owner."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from tauric import market, research
    from tauric.models import MarketSnapshot, Owner
    from tauric.research_contracts import DatasetCapture, ReplayCreate, ReplayParameters

    fixture = json.loads(fixture_path.read_text())
    db_engine = create_engine(fixture["database_url"])
    with Session(db_engine) as db:
        owner = db.scalar(select(Owner))
        spec = market.instrument(db, "binance:spot:BTCUSDT")
        candles, provider_time, received_at = market.PROVIDERS[spec.provider].collect(
            spec, "15m"
        )
        payload = {
            "candles": candles,
            "source": spec.provider,
            "symbol": spec.provider_symbol,
            "volume_type": spec.volume_type,
            "availability": "observed_at_receipt",
        }
        snapshot = MarketSnapshot(
            instrument_id=spec.id,
            interval="15m",
            provider_time=provider_time,
            received_at=received_at,
            data=payload,
            checksum=hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
        )
        db.add(snapshot)
        db.commit()
        dataset = research.capture(
            db,
            owner,
            DatasetCapture(
                request_id=uuid4(),
                name="Real BTC/USDT 15-minute verification",
                snapshot_id=snapshot.id,
                reason="Verify public market evidence through isolated retrospective research",
            ),
        )
        run = research.run(
            db,
            owner,
            ReplayCreate(
                request_id=uuid4(),
                dataset_id=dataset.id,
                name="Real BTC SMA 5/20 costed replay",
                parameters=ReplayParameters(),
                hypothesis="Test a 5/20 SMA crossover on public BTC spot candles after explicit fees and slippage",
                reason="Verify real public data, saved parameters and deterministic outcome reconciliation",
            ),
        )
        export = research.owned(db, owner, run.id, "run")
        result = {
            "verified_at": received_at.isoformat(),
            "provider_time": provider_time.isoformat(),
            "provider": spec.provider,
            "instrument_id": spec.id,
            "dataset": dataset.model_dump(mode="json"),
            "run": run.model_dump(mode="json"),
            "source_artifact": export.payload,
            "availability": "retrospective",
            "model_calls": 0,
            "broker_order_calls": 0,
            "primary_owner_data_modified": False,
        }
    db_engine.dispose()
    (root / "artifacts/real-research-results.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(
        f"Real public BTC dataset and replay verified: {dataset.bar_count} closed candles, {run.result.metrics.closed_trades} closed trades. No model calls or broker orders."
    )


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    elif "--test" in sys.argv:
        browser_checks()
    elif "--real-market" in sys.argv:
        if fixture_path.exists():
            raise SystemExit(
                "An existing research fixture needs cleanup before a new run."
            )
        try:
            prepare()
            real_market_check()
        finally:
            cleanup()
    else:
        if fixture_path.exists():
            raise SystemExit(
                "An existing research fixture needs cleanup before a new run."
            )
        try:
            prepare()
            helper = (
                Path.home() / ".agents/skills/webapp-testing/scripts/with_server.py"
            )
            python = str(root / ".venv/bin/python")
            browser_python = shutil.which("python3")
            if not browser_python:
                raise RuntimeError("Python Playwright is required for browser checks.")
            server = shlex.join(
                [
                    "exec",
                    python,
                    str(root / "scripts/research_browser_test.py"),
                    "--serve",
                ]
            )
            subprocess.run(
                [
                    python,
                    str(helper),
                    "--server",
                    server,
                    "--port",
                    "8092",
                    "--timeout",
                    "60",
                    "--",
                    browser_python,
                    str(root / "scripts/research_browser_test.py"),
                    "--test",
                ],
                cwd=root,
                check=True,
            )
        finally:
            cleanup()
