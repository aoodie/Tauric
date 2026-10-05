"""Run only against the test's disposable server and synthetic collectors."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    chart = page.locator(".desktop-layout .market-chart-panel")
    expect(chart.get_by_label("Chart instrument")).to_have_value("binance:spot:BTCUSDT")
    chart.get_by_label("Chart instrument").select_option("oanda:fx:EUR_USD")
    chart.get_by_role("button", name="Refresh market", exact=True).click()
    expect(chart.locator(".market-foot")).to_contain_text("7 candles")
    expect(chart.locator(".market-readout")).to_contain_text("Ticks")
    expect(
        chart.get_by_role("button", name="Daily research", exact=True)
    ).to_be_enabled()
    expect(chart).to_contain_text("Data credentials needed")
    chart.locator("summary").filter(has_text="Data quality").click()
    expect(chart).to_contain_text("Explicit offline browser fixture")
    checks.append(
        "FX chart preserves tick-count label, source warnings and daily research entry"
    )
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_label("Dataset name", exact=True).fill("Browser FX evidence")
    page.get_by_role("button", name="Save chart dataset", exact=True).click()
    expect(page.get_by_label("Saved dataset")).to_contain_text("Browser FX evidence")
    expect(page.locator(".research-provenance")).to_contain_text("fx · USD")
    page.get_by_label("Fast average · candles", exact=True).fill("1")
    page.get_by_label("Slow average · candles", exact=True).fill("2")
    page.get_by_label("Full spread · basis points", exact=True).fill("0")
    page.get_by_label("Daily FX financing · basis points", exact=True).fill("1")
    page.get_by_role("button", name="Run replay", exact=True).click()
    expect(page.locator(".research-result")).to_be_visible()
    expect(page.locator(".research-result")).to_contain_text(
        "FX financing debit / credit"
    )
    expect(page.locator(".research-result")).to_contain_text("Net P/L · USD")
    expect(page.locator(".research-result")).not_to_contain_text("USDT")
    checks.append(
        "FX evidence captures price/calendar semantics and runs explicit-cost replay in USD"
    )
    page.get_by_role("button", name="Workspace", exact=True).click()
    chart.get_by_label("Chart instrument").select_option("alpaca:equity:AAPL")
    chart.get_by_role("button", name="Refresh market", exact=True).click()
    expect(chart.locator(".market-foot")).to_contain_text("7 candles")
    expect(chart).to_contain_text("Alpaca stocks / ETFs")
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_label("Dataset name", exact=True).fill("Browser stock evidence")
    page.get_by_role("button", name="Save chart dataset", exact=True).click()
    expect(page.get_by_label("Saved dataset")).to_contain_text("Browser stock evidence")
    expect(page.locator(".research-provenance")).to_contain_text("equity · USD")
    page.get_by_label("Fast average · candles", exact=True).fill("1")
    page.get_by_label("Slow average · candles", exact=True).fill("2")
    page.get_by_label("Full spread · basis points", exact=True).fill("2")
    page.get_by_label("Commission per unit / share", exact=True).fill("0.01")
    page.get_by_label("Minimum commission per fill", exact=True).fill("1")
    page.get_by_label("Sell-side tax · basis points", exact=True).fill("1")
    page.get_by_role("button", name="Run replay", exact=True).click()
    expect(page.locator(".research-result")).to_contain_text("equity experiment")
    checks.append(
        "Stock replay accepts spread, per-share/minimum commissions and sell tax"
    )
    page.screenshot(
        path=str(root / "artifacts/market-adapters-research.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_label("Full spread · basis points", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    checks.append("Asset cost controls remain usable on mobile")
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_label("Toggle navigation", exact=True).click()
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    expect(page.get_by_label("Saved dataset")).to_contain_text("Browser stock evidence")
    checks.append("Datasets and costed results survive browser reload")
    assert not errors, errors
    browser.close()
(root / "artifacts/market-adapters-browser-results.json").write_text(
    json.dumps(
        {"synthetic_data": True, "provider_calls": 0, "checks": checks}, indent=2
    )
)
print(f"{len(checks)} FX/stock browser checks passed")
