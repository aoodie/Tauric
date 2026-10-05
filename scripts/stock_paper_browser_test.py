"""Synthetic disposable stock workstation verification; no live providers/models."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url = sys.argv[1]
fixture = json.loads(sys.argv[2])
if not url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable test server")
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.locator(".flexlayout__tab_button").filter(has_text="Scanner").click()
    page.get_by_role("tab", name="Stocks", exact=True).click()
    expect(
        page.get_by_role("article", name="alpaca:equity:AAPL candidate", exact=True)
    ).to_be_visible()
    page.get_by_role("tab", name="Forex", exact=True).click()
    expect(
        page.get_by_role("article", name="alpaca:equity:AAPL candidate", exact=True)
    ).to_have_count(0)
    page.get_by_role("tab", name="Stocks", exact=True).click()
    results.append(
        "Stock scanner filter displays persisted evidence and separates other asset families"
    )
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    card = page.get_by_role("article", name="Stock breakout versions", exact=True)
    card.get_by_role("button", name="Deploy saved version", exact=True).click()
    form = card.get_by_role(
        "form", name="Stock breakout version deployment", exact=True
    )
    form.get_by_label("Account and allocation", exact=True).select_option(
        label="Stock cash / Test portfolio"
    )
    form.get_by_label("Deployment instrument", exact=True).select_option(
        "alpaca:etf:SPY"
    )
    for label, value in {
        "Stock spread assumption (bps)": "2",
        "Commission per share (USD)": "0.02",
        "Minimum commission per fill (USD)": "1",
        "Sell-side tax assumption (bps)": "3",
        "Deployment reason": "Synthetic ETF browser route",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_role("button", name="Deploy version", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Version deployed")
    data = page.evaluate("async()=>await(await fetch('/api/v1/paper')).json()")
    etf = next(d for d in data["deployments"] if d["instrument_id"] == "alpaca:etf:SPY")
    assert (
        etf["stock_costs"]["asset_type"] == "etf"
        and etf["stock_costs"]["minimum_commission"] == "1"
    )
    results.append("Saved-version ETF deployment persists explicit USD share costs")
    page.get_by_role("button", name="Paper trading", exact=True).click()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(
        fixture["deployment_id"]
    )
    expect(form.get_by_label("Original native report", exact=True)).to_have_attribute(
        "required", ""
    )
    expect(page.get_by_label("Reviewer", exact=True)).to_have_value("cloud")
    autonomy = page.get_by_role(
        "article", name="Autonomous paper execution", exact=True
    )
    expect(
        autonomy.get_by_label("Automatic paper entries", exact=True)
    ).to_be_disabled()
    expect(autonomy).to_contain_text("exact-version strategy assessment")
    page.get_by_label("Recorded proposal", exact=True).select_option(
        fixture["proposal_id"]
    )
    expect(page.get_by_text("ALLOW · OPENCLAW REVIEW", exact=True)).to_be_visible()
    page.get_by_role("button", name="Execute paper plan", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper fill recorded")
    expect(
        page.get_by_role("button", name="Close paper position", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Close paper position", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper position closed")
    data = page.evaluate("async()=>await(await fetch('/api/v1/paper')).json()")
    assert not data["positions"]
    acct = next(a for a in data["accounts"] if a["id"] == fixture["account_id"])
    assert acct["currency"] == "USD" and acct["reconciled"]
    results.append(
        "Bound reviewed stock plan executes and closes with a reconciled USD journal"
    )
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.locator(".flexlayout__tab_button").filter(has_text="Scanner").click()
    page.get_by_role("tab", name="Stocks", exact=True).click()
    expect(
        page.get_by_role("article", name="alpaca:equity:AAPL candidate", exact=True)
    ).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("scanner")
    page.get_by_role("tab", name="Stocks", exact=True).click()
    expect(page.get_by_role("tab", name="Stocks", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_role("tab", name="Stocks", exact=True)).to_be_visible()
    page.screenshot(path=str(root / "artifacts/stock-paper-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1")
    results.append("Stock evidence survives reload and mobile layout")
    assert not errors, errors
    (root / "artifacts/stock-paper-browser-results.json").write_text(
        json.dumps({"passed": results, "browser_errors": errors}, indent=2) + "\n"
    )
    browser.close()
print(json.dumps({"passed": results}, indent=2))
