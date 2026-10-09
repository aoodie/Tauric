"""Built chart-to-report workflow against a disposable, offline fixture server."""

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
    panel = page.locator(".desktop-layout .native-panel")
    for instrument, symbol in (
        ("alpaca:equity:AAPL", "AAPL"),
        ("alpaca:etf:SPY", "SPY"),
    ):
        chart.get_by_label("Chart instrument").select_option(instrument)
        expect(
            chart.get_by_role("button", name="Research", exact=True)
        ).to_be_enabled()
        chart.get_by_role("button", name="Research", exact=True).click()
        expect(panel.locator(".native-scope strong")).to_have_text(symbol)
        expect(panel.locator(".native-scope")).to_contain_text(
            "Alpaca chart snapshots are separate"
        )
        if symbol == "SPY":
            expect(panel.locator(".native-scope")).to_contain_text(
                "company fundamentals are omitted"
            )
        panel.get_by_role("button", name="Run daily analysis", exact=True).click()
        expect(panel.locator(".native-report")).to_be_visible()
        expect(panel.locator(".native-rating strong")).to_have_text("Hold")
        panel.get_by_label("Native report section").select_option("market_report")
        expect(panel.locator(".native-report .report-prose")).to_contain_text(
            f"Offline browser fixture for {symbol}"
        )
        checks.append(
            f"{symbol} chart selection, scoped daily request and saved report"
        )
    page.screenshot(
        path=str(root / "artifacts/stock-daily-research.png"), full_page=True
    )
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    chart.get_by_label("Chart instrument").select_option("alpaca:etf:SPY")
    expect(panel.locator(".native-report")).to_be_visible()
    checks.append("Saved ETF report remains available after reload")
    chart.get_by_label("Chart instrument").select_option("oanda:fx:EUR_USD")
    expect(
        chart.get_by_role("button", name="Research", exact=True)
    ).to_be_enabled()
    expect(
        panel.get_by_role("button", name="Run daily analysis", exact=True)
    ).to_be_disabled()
    expect(panel.locator(".native-report")).not_to_be_visible()
    checks.append(
        "FX requires OANDA credentials and stock reports do not leak into another instrument"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("chart")
    page.locator(".mobile-panel").get_by_label("Chart instrument").select_option(
        "alpaca:etf:SPY"
    )
    page.locator(".mobile-panel").get_by_role(
        "button", name="Research", exact=True
    ).click()
    expect(page.locator(".mobile-panel .native-report")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    checks.append("Daily research and saved ETF report work on mobile")
    assert not errors, errors
    browser.close()
(root / "artifacts/stock-daily-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "model_calls": 0,
            "provider_calls": 0,
            "synthetic_reports": True,
        },
        indent=2,
    )
)
print(f"{len(checks)} stock/ETF daily browser checks passed")
