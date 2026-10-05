"""Browser-only checks; requires the disposable pytest-hosted synthetic server."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
base_url = sys.argv[1]
if not base_url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable local browser test server.")
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    # Existing account/allocation setup is covered separately; seed this disposable route.
    page.evaluate("""async () => {
      const s = await (await fetch('/api/v1/session')).json();
      window.rulePost = async (path, body) => {
        const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token}, body:JSON.stringify(body)});
        const value = await r.json(); if (!r.ok) throw new Error(JSON.stringify(value)); return value;
      };
      const a = await window.rulePost('/api/v1/paper/accounts', {request_id:crypto.randomUUID(), name:'Rules browser account', currency:'USDT', starting_balance:'10000', reason:'Disposable synthetic browser account', policy:{risk_percent:'1',max_total_risk_percent:'3',max_exposure_percent:'80',max_positions:5,fee_bps:'10',slippage_bps:'5',quote_max_age_seconds:60,decision_ttl_seconds:300}});
      await window.rulePost('/api/v1/paper/capital', {request_id:crypto.randomUUID(), account_id:a.id, kind:'portfolio', parent_id:null, name:'Rules browser allocation', limits:{budget:'10000',max_total_risk_percent:'3',max_positions:5},reason:'Disposable browser allocation'});
    }""")
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.get_by_role("button", name="Add strategy version", exact=True).click()
    form = page.get_by_role("form", name="New strategy version", exact=True)
    for label, value in {
        "Registered strategy name": "Browser executable",
        "Version label": "v1",
        "Version quantity step": "0.0001",
        "Version capital limit (account currency)": "5000",
        "Version risk per trade (%)": "1",
        "Strategy notes": "Synthetic closed candle browser workflow.",
        "Version reason": "Save executable browser rules",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_label("Entry and exit rules", exact=True).select_option(
        "closed_bar_breakout_v1"
    )
    form.get_by_label("Entry lookback (closed candles)", exact=True).fill("3")
    form.get_by_label("Exit lookback (closed candles)", exact=True).fill("2")
    form.get_by_label("Minimum volume ratio", exact=True).fill("2")
    form.get_by_role("button", name="Save strategy version", exact=True).click()
    card = page.get_by_role("article", name="Browser executable versions", exact=True)
    expect(card.get_by_text("Revision 1", exact=True)).to_be_visible()
    card.get_by_role("button", name="Deploy saved version", exact=True).click()
    form = card.get_by_role(
        "form", name="Browser executable version deployment", exact=True
    )
    form.get_by_label("Account and allocation", exact=True).select_option(
        label="Rules browser account / Rules browser allocation"
    )
    form.get_by_label("Deployment instrument", exact=True).select_option(
        "binance:spot:BTCUSDT"
    )
    form.get_by_label("Deployment reason", exact=True).fill(
        "Deploy synthetic browser strategy"
    )
    form.get_by_role("button", name="Deploy version", exact=True).click()
    expect(page.get_by_text("Browser executable · v1", exact=True)).to_be_visible()
    results.append("Saved executable rule fields and deployed the pinned version")
    dep = page.evaluate(
        "async()=>{const s=await(await fetch('/api/v1/paper')).json();return s.deployments.find(d=>d.strategy==='Browser executable');}"
    )
    page.get_by_role("button", name="Paper trading", exact=True).click()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(dep["id"])
    expect(
        form.get_by_role("button", name="Record paper proposal", exact=True)
    ).to_be_disabled()
    monitor = page.get_by_role("article", name="Paper strategy monitoring", exact=True)
    schedule_form = monitor.get_by_role(
        "form", name="Paper monitoring schedule", exact=True
    )
    schedule_form.get_by_label("Enable scheduled monitoring", exact=True).check()
    schedule_form.get_by_label("Check every (seconds)", exact=True).fill("60")
    schedule_form.get_by_label("Monitoring change reason", exact=True).fill(
        "Enable disposable browser monitoring"
    )
    schedule_form.get_by_role(
        "button", name="Save monitoring schedule", exact=True
    ).click()
    expect(monitor).to_contain_text("Monitoring enabled")
    expect(page.get_by_role("region", name="Strategy rule result")).to_contain_text(
        "ENTER", timeout=15000
    )
    results.append("Saved opt-in monitoring and received a background entry evaluation")
    expect(form.get_by_label("Stop reference (USDT)", exact=True)).to_have_value("90")
    assert (
        form.get_by_label("Stop reference (USDT)", exact=True).get_attribute("readonly")
        is not None
    )
    form.get_by_label("Proposal reason", exact=True).fill(
        "Review synthetic executable entry"
    )
    form.get_by_role("button", name="Record paper proposal", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper proposal recorded")
    page.get_by_label("Review outcome", exact=True).select_option("allow")
    page.get_by_label("Review reason", exact=True).fill("Allow synthetic rule proposal")
    page.get_by_role("button", name="Save owner review", exact=True).click()
    expect(page.get_by_text("ALLOW · OWNER REVIEW", exact=True)).to_be_visible()
    page.get_by_role("button", name="Build paper plan", exact=True).click()
    page.get_by_role("button", name="Execute paper plan", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper fill recorded")
    state = page.evaluate("async()=>await(await fetch('/api/v1/paper')).json()")
    assert len(state["positions"]) == 1
    entry = next(
        r
        for r in state["records"]
        if r["kind"] == "fill" and r["payload"]["side"] == "buy"
    )
    assert entry["payload"]["lineage"]["strategy_run_id"]
    results.append(
        "Evaluated closed bars and completed reviewed entry with linked rule evidence"
    )
    page.reload()
    # The authenticated workstation polls continuously; wait for its rendered shell.
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Paper trading", exact=True).click()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(dep["id"])
    monitor = page.get_by_role("article", name="Paper strategy monitoring", exact=True)
    expect(monitor).to_contain_text("Monitoring enabled")
    expect(page.get_by_role("region", name="Strategy rule result")).to_contain_text(
        "ENTER"
    )
    page.get_by_role("button", name="Evaluate strategy rules", exact=True).click()
    expect(page.get_by_role("region", name="Strategy rule result")).to_contain_text(
        "HOLD"
    )
    expect(
        form.get_by_role("button", name="Record paper proposal", exact=True)
    ).to_be_disabled()
    results.append(
        "Evidence survives reload and an open position blocks further entries"
    )
    autonomy = page.get_by_role(
        "article", name="Autonomous paper execution", exact=True
    )
    expect(autonomy).to_contain_text("Autonomous paper off")
    execution_form = autonomy.get_by_role(
        "form", name="Autonomous paper settings", exact=True
    )
    execution_form.get_by_label("Enable autonomous paper execution", exact=True).check()
    execution_form.get_by_label("Automatic paper entries", exact=True).uncheck()
    execution_form.get_by_label("Automatic attributed exits", exact=True).check()
    execution_form.get_by_label("New research runs per day", exact=True).fill("0")
    execution_form.get_by_label("OpenClaw reviews per day", exact=True).fill("0")
    execution_form.get_by_label("Execution settings reason", exact=True).fill(
        "Enable disposable automatic attributed exits"
    )
    execution_form.get_by_role(
        "button", name="Save autonomous paper settings", exact=True
    ).click()
    expect(autonomy).to_contain_text("Autonomous paper enabled")
    results.append(
        "Enabled an explicit exit-only paper policy without model admissions"
    )
    page.evaluate(
        """async()=>{
          const s=await(await fetch('/api/v1/session')).json();
          const r=await fetch('/api/v1/test-strategy-feed/advance',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:'{}'});
          if(!r.ok)throw new Error('Could not advance disposable candles');
        }"""
    )
    expect(page.get_by_role("region", name="Strategy rule result")).to_contain_text(
        "EXIT", timeout=15000
    )
    results.append(
        "Monitoring survived reload and detected the attributed exit in the background"
    )
    page.screenshot(
        path=str(root / "artifacts/strategy-rules-paper.png"), full_page=True
    )
    expect(
        autonomy.get_by_label("Autonomous paper attempts", exact=True)
    ).to_contain_text("Paper position closed", timeout=15000)
    state = page.evaluate("async()=>await(await fetch('/api/v1/paper')).json()")
    assert state["positions"] == [] and state["accounts"][0]["reconciled"]
    exit_fill = next(
        r
        for r in state["records"]
        if r["kind"] == "fill" and r["payload"]["side"] == "sell"
    )
    assert exit_fill["payload"]["position_id"] == entry["id"]
    assert exit_fill["payload"]["strategy_exit_run_id"]
    assert exit_fill["payload"]["exit_source"] == "autonomous_rule_close"
    results.append(
        "Unattended attributed exit filled at the observed paper price and reconciled"
    )
    execution_form.get_by_label(
        "Enable autonomous paper execution", exact=True
    ).uncheck()
    execution_form.get_by_label("Execution settings reason", exact=True).fill(
        "Stop disposable autonomous execution"
    )
    execution_form.get_by_role(
        "button", name="Save autonomous paper settings", exact=True
    ).click()
    expect(autonomy).to_contain_text("Autonomous paper off")
    autonomy.get_by_role(
        "button", name="Load paper execution history", exact=True
    ).click()
    expect(
        autonomy.get_by_label("Autonomous paper history", exact=True)
    ).to_contain_text("Stop disposable autonomous execution")
    expect(
        autonomy.get_by_label("Autonomous paper history", exact=True)
    ).to_contain_text("exit filled")
    results.append(
        "Disabled execution and inspected persisted policy and fill receipts"
    )
    schedule_form = monitor.get_by_role(
        "form", name="Paper monitoring schedule", exact=True
    )
    schedule_form.get_by_label("Enable scheduled monitoring", exact=True).uncheck()
    schedule_form.get_by_label("Monitoring change reason", exact=True).fill(
        "Stop disposable browser monitoring"
    )
    schedule_form.get_by_role(
        "button", name="Save monitoring schedule", exact=True
    ).click()
    expect(monitor).to_contain_text("Monitoring off")
    monitor.get_by_role("button", name="Load monitoring history", exact=True).click()
    expect(monitor.get_by_label("Monitoring history", exact=True)).to_contain_text(
        "Stop disposable browser monitoring"
    )
    expect(monitor.get_by_label("Monitoring history", exact=True)).to_contain_text(
        "EXIT"
    )
    results.append(
        "Stopped monitoring and inspected immutable schedule and check history"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(
        path=str(root / "artifacts/strategy-rules-mobile.png"), full_page=True
    )
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    assert not errors, errors
    results.append("Mobile layout fits and the browser reports no application errors")
    browser.close()
(root / "artifacts/strategy-rules-browser-results.json").write_text(
    json.dumps({"synthetic_candles": True, "checks": results}, indent=2)
)
print(json.dumps(results))
