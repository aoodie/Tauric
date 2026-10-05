"""Saved versions, explicit paper routing, pause and preserved opening lineage."""

import json
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
results = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    account = page.locator(".paper-card").filter(
        has=page.get_by_role("heading", name="Browser paper", exact=True)
    )
    account.get_by_role("button", name="Resume paper entries", exact=True).click()
    page.get_by_role("button", name="Add strategy version", exact=True).click()
    form = page.get_by_role("form", name="New strategy version", exact=True)
    for label, value in {
        "Registered strategy name": "Browser registered",
        "Version label": "v1",
        "Version quantity step": "0.00001",
        "Version capital limit (USDT)": "5000",
        "Version risk per trade (%)": "1",
        "Strategy notes": "Browser original strategy notes. Explicit long spot proposals only.",
        "Version reason": "Save browser immutable first version",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_role("button", name="Save strategy version", exact=True).click()
    card = page.get_by_role("article", name="Browser registered versions", exact=True)
    expect(card.get_by_text("Revision 1", exact=True)).to_be_visible()
    card.get_by_role("button", name="Deploy saved version", exact=True).click()
    form = card.get_by_role(
        "form", name="Browser registered version deployment", exact=True
    )
    form.get_by_label("Account and allocation", exact=True).select_option(
        label="Browser paper / Browser portfolio / Browser capital"
    )
    form.get_by_label("Deployment instrument", exact=True).select_option(
        "binance:spot:BTCUSDT"
    )
    form.get_by_label("Deployment reason", exact=True).fill(
        "Deploy original browser paper route"
    )
    form.get_by_role("button", name="Deploy version", exact=True).click()
    expect(page.get_by_text("Browser registered · v1", exact=True)).to_be_visible()
    results.append(
        "Immutable strategy version saved and routed to an explicit account/allocation"
    )
    card.get_by_role("button", name="Create new version", exact=True).click()
    form = card.get_by_role("form", name="Browser registered new version", exact=True)
    form.get_by_label("Version label", exact=True).fill("v2")
    form.get_by_label("Version risk per trade (%)", exact=True).fill("0.25")
    form.get_by_label("Strategy notes", exact=True).fill(
        "Browser revised notes for a lower risk version."
    )
    form.get_by_label("Version reason", exact=True).fill(
        "Prepare lower risk without changing the route"
    )
    form.get_by_role("button", name="Save new version", exact=True).click()
    expect(card.get_by_text("Revision 2", exact=True)).to_be_visible()
    card.locator("summary").click()
    expect(card.get_by_text("v1 · revision 1", exact=True)).to_be_visible()
    expect(card.get_by_text("v2 · revision 2", exact=True)).to_be_visible()
    expect(page.get_by_text("Browser registered · v1", exact=True)).to_be_visible()
    deployment = page.evaluate(
        "async()=>{const s=await(await fetch('/api/v1/paper')).json();return s.deployments.find(d=>d.strategy==='Browser registered');}"
    )
    assert deployment["risk_percent"] == "1"
    results.append(
        "A new version preserves the earlier deployment and complete history"
    )
    page.get_by_role("button", name="Paper trading", exact=True).click()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(deployment["id"])
    quote = page.evaluate(
        """async()=>{const s=await(await fetch('/api/v1/session')).json();const r=await fetch('/api/v1/market/binance:spot:BTCUSDT/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:JSON.stringify({interval:'1m'})});if(!r.ok)throw new Error('Public price refresh failed');return r.json();}"""
    )
    stop = str(
        (Decimal(quote["candles"][-1]["close"]) * Decimal("0.9")).quantize(
            Decimal("0.01")
        )
    )
    form.get_by_label("Stop reference (USDT)", exact=True).fill(stop)
    form.get_by_label("Proposal reason", exact=True).fill(
        "Browser pinned version paper entry"
    )
    form.get_by_role("button", name="Record paper proposal", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper proposal recorded")
    page.get_by_label("Review outcome", exact=True).select_option("allow")
    page.get_by_label("Review reason", exact=True).fill("Owner browser version review")
    page.get_by_role("button", name="Save owner review", exact=True).click()
    expect(page.get_by_text("ALLOW · OWNER REVIEW", exact=True)).to_be_visible()
    page.get_by_role("button", name="Build paper plan", exact=True).click()
    page.get_by_role("button", name="Execute paper plan", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper fill recorded")
    entry = page.evaluate(
        "async()=>{const s=await(await fetch('/api/v1/paper')).json();return s.records.find(r=>r.kind==='fill'&&r.payload.side==='buy'&&r.deployment_id==='"
        + deployment["id"]
        + "');}"
    )
    assert (
        entry["payload"]["lineage"]["strategy_revision_id"]
        == deployment["strategy_revision_id"]
    )
    results.append(
        "Paper workflow uses the pinned original version after a newer version is saved"
    )
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    route = page.locator(".paper-deployment").filter(
        has=page.get_by_text("Browser registered · v1", exact=True)
    )
    route.get_by_role("button", name="Pause strategy route", exact=True).click()
    form = route.get_by_role(
        "form", name="Browser registered route control", exact=True
    )
    form.get_by_label("Route change reason", exact=True).fill(
        "Browser pause while position remains open"
    )
    form.get_by_role("button", name="Confirm pause", exact=True).click()
    expect(
        route.get_by_role("button", name="Resume strategy route", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Paper trading", exact=True).click()
    page.get_by_role("button", name="Close paper position", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper position closed")
    exit_lineage = page.evaluate(
        "async()=>{const s=await(await fetch('/api/v1/paper')).json();return s.records.find(r=>r.kind==='fill'&&r.payload.side==='sell'&&r.deployment_id==='"
        + deployment["id"]
        + "').payload.lineage;}"
    )
    assert exit_lineage == entry["payload"]["lineage"]
    results.append(
        "Paused route retains its open position and allows a close with original lineage"
    )
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    expect(
        route.get_by_role("button", name="Resume strategy route", exact=True)
    ).to_be_visible()
    page.screenshot(path=str(root / "artifacts/strategy-settings.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(
        card.get_by_role("button", name="Deploy saved version", exact=True)
    ).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    assert not errors, errors
    page.screenshot(path=str(root / "artifacts/strategy-mobile.png"), full_page=True)
    results.append(
        "Version history and paused route persist after reload and remain usable on mobile"
    )
    browser.close()
(root / "artifacts/strategy-browser-results.json").write_text(
    json.dumps(
        {"passed": results, "real_model_calls": 0, "broker_order_calls": 0}, indent=2
    )
)
print(f"{len(results)} strategy browser checks passed")
