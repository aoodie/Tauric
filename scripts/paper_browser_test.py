"""Real public prices, disposable paper balances; no AI or broker order calls."""

import json
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.get_by_role("button", name="Add paper account", exact=True).click()
    form = page.get_by_role("form", name="New paper account", exact=True)
    values = {
        "Account name": "Browser paper",
        "Starting balance (USDT)": "10000",
        "Risk per trade (%)": "1",
        "Total open risk (%)": "3",
        "Maximum exposure (%)": "80",
        "Maximum open positions": "5",
        "Fee (basis points)": "10",
        "Slippage (basis points)": "5",
        "Maximum price age (seconds, 1–60)": "60",
        "Decision valid for (seconds, 1–3600)": "300",
        "Setup reason": "Browser paper setup",
    }
    for key, value in values.items():
        form.get_by_label(key, exact=True).fill(value)
    form.get_by_role("button", name="Create paper account", exact=True).click()
    expect(
        page.get_by_role("heading", name="Browser paper", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Add portfolio", exact=True).click()
    allocation_form = page.get_by_role(
        "form", name="New capital allocation", exact=True
    )
    for key, value in {
        "Allocation name": "Browser portfolio",
        "Allocation budget (USDT)": "10000",
        "Allocation total risk (%)": "3",
        "Allocation maximum positions": "5",
        "Allocation setup reason": "Browser shared budget",
    }.items():
        allocation_form.get_by_label(key, exact=True).fill(value)
    allocation_form.get_by_role("button", name="Create allocation", exact=True).click()
    expect(
        page.get_by_role("heading", name="Browser portfolio", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Add capital pot", exact=True).click()
    allocation_form = page.get_by_role(
        "form", name="New capital allocation", exact=True
    )
    allocation_form.get_by_label("Parent allocation", exact=True).select_option(
        label="Browser portfolio"
    )
    for key, value in {
        "Allocation name": "Browser capital",
        "Allocation budget (USDT)": "10000",
        "Allocation total risk (%)": "3",
        "Allocation maximum positions": "5",
        "Allocation setup reason": "Browser child budget",
    }.items():
        allocation_form.get_by_label(key, exact=True).fill(value)
    allocation_form.get_by_role("button", name="Create allocation", exact=True).click()
    expect(
        page.get_by_role(
            "heading", name="Browser portfolio / Browser capital", exact=True
        )
    ).to_be_visible()
    page.get_by_role("button", name="Add paper strategy", exact=True).click()
    form = page.get_by_role("form", name="New paper strategy", exact=True)
    form.get_by_label("Instrument", exact=True).select_option("binance:spot:BTCUSDT")
    form.get_by_label("Shared capital allocation", exact=True).select_option(
        label="Browser portfolio / Browser capital"
    )
    for key, value in {
        "Strategy name": "Browser strategy",
        "Strategy version": "v1",
        "Strategy capital limit (USDT)": "10000",
        "Strategy risk per trade (%)": "1",
        "Paper quantity step": "0.00001",
        "Setup reason": "Browser strategy setup",
    }.items():
        form.get_by_label(key, exact=True).fill(value)
    form.get_by_role("button", name="Create paper strategy", exact=True).click()
    expect(page.get_by_text("Browser strategy · v1", exact=True)).to_be_visible()
    page.screenshot(path=str(root / "artifacts/paper-settings.png"), full_page=True)
    results.append(
        "Settings accounts, explicit risk/cost policy and attributed deployment saved"
    )
    page.get_by_role("button", name="Trading", exact=True).click()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(index=1)
    # Fetch the real public price through the authenticated platform route.
    quote = page.evaluate(
        """async () => {const s=await (await fetch('/api/v1/session')).json(); const r=await fetch('/api/v1/market/binance:spot:BTCUSDT/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token},body:JSON.stringify({interval:'1m'})}); if(!r.ok)throw new Error('Public price refresh failed'); return await r.json();}"""
    )
    stop = str(
        (Decimal(quote["candles"][-1]["close"]) * Decimal("0.9")).quantize(
            Decimal("0.01")
        )
    )
    form.get_by_label("Stop reference (USDT)", exact=True).fill(stop)
    form.get_by_label("Proposal reason", exact=True).fill("Browser owner paper test")
    form.get_by_role("button", name="Record paper proposal", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper proposal recorded")
    page.get_by_label("Review outcome", exact=True).select_option("allow")
    page.get_by_label("Review reason", exact=True).fill(
        "Owner reviewed costs and paper risk"
    )
    page.get_by_role("button", name="Save owner review", exact=True).click()
    expect(page.get_by_text("ALLOW · OWNER REVIEW", exact=True)).to_be_visible()
    page.get_by_role("button", name="Build paper plan", exact=True).click()
    expect(
        page.get_by_role("button", name="Execute paper plan", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Execute paper plan", exact=True).click()
    expect(
        page.get_by_text("Simulated fill recorded in the paper ledger.", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role("button", name="Close paper position", exact=True)
    ).to_be_visible()
    page.screenshot(path=str(root / "artifacts/paper-workflow.png"), full_page=True)
    results.append(
        "Real public price to owner review, deterministic size, simulated fill and attributed position"
    )
    page.get_by_role("button", name="Close paper position", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Paper position closed")
    expect(
        page.get_by_role("button", name="Close paper position", exact=True)
    ).not_to_be_visible()
    page.reload()
    page.get_by_role("button", name="Trading", exact=True).click()
    expect(page.get_by_text("No open paper positions.", exact=True)).to_be_visible()
    data = page.evaluate("async () => await (await fetch('/api/v1/paper')).json()")
    account = data["accounts"][0]
    assert (
        account["reconciled"]
        and len([r for r in data["records"] if r["kind"] == "fill"]) == 2
    )
    assert Decimal(account["cash"]) == Decimal(account["starting_balance"]) + Decimal(
        account["realized_pnl"]
    )
    assert data["live_enabled"] is False
    results.append(
        "Explicit close, entry/exit costs, reconciled cash and restart-persistent journal"
    )
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.get_by_role("button", name="Halt new entries", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("New paper entries halted")
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    expect(
        page.get_by_role("button", name="Resume paper entries", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Edit risk policy", exact=True).click()
    form = page.get_by_role("form", name="Browser paper risk policy", exact=True)
    form.get_by_label("Risk per trade (%)", exact=True).fill("0.5")
    form.get_by_label("Change reason", exact=True).fill("Browser reduce risk")
    form.get_by_role("button", name="Save risk policy", exact=True).click()
    expect(page.get_by_text("Policy v2", exact=False)).to_be_visible()
    results.append("Persisted halt and versioned editable risk policy")
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(
        path=str(root / "artifacts/paper-settings-mobile.png"), full_page=True
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    results.append("Mobile layout without horizontal overflow or page errors")
    browser.close()
(root / "artifacts/paper-browser-results.json").write_text(
    json.dumps({"passed": results}, indent=2)
)
print(f"{len(results)} paper browser flows passed")
