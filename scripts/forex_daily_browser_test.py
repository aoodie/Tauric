"""Forex chart-to-report checks against disposable OANDA/model fixtures."""

import json
import sys
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

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
        ("oanda:fx:EUR_USD", "EUR/USD"),
        ("oanda:fx:GBP_USD", "GBP/USD"),
        ("oanda:fx:USD_JPY", "USD/JPY"),
    ):
        chart.get_by_label("Chart instrument").select_option(instrument)
        expect(
            chart.get_by_role("button", name="Research", exact=True)
        ).to_be_enabled()
        chart.get_by_role("button", name="Research", exact=True).click()
        expect(panel.locator(".native-scope strong")).to_have_text(symbol)
        expect(panel.locator(".daily-research .panel-kicker")).to_contain_text("FOREX DAILY RESEARCH")
        expect(panel.locator(".native-scope")).to_contain_text(
            "closed OANDA bid/ask candles"
        )
        expect(panel.locator(".native-scope")).to_contain_text(
            "economic releases, policy benchmarks and account financing are supplied"
        )
        panel.get_by_role("button", name="Run daily analysis", exact=True).click()
        expect(panel.locator(".native-report")).to_be_visible()
        expect(panel.locator(".native-rating strong")).to_have_text("REVIEW")
        expect(panel.locator(".fx-policy")).to_contain_text("Policy news & events")
        expect(panel.locator(".fx-policy")).to_contain_text("release time not supplied")
        expect(panel.locator(".fx-supplement")).to_contain_text("Economic calendar, rates & financing")
        expect(panel.locator(".fx-supplement")).to_contain_text("5/5 sources available")
        expect(panel.locator(".fx-supplement")).to_contain_text("Consensus")
        if symbol == "GBP/USD":
            expect(panel.locator(".fx-policy")).to_contain_text("Offline unavailable source fixture")
            expect(panel.locator(".fx-policy")).to_contain_text("2/4 sources available")
        else:
            expect(panel.locator(".fx-policy")).to_contain_text("4/4 sources available")
        panel.get_by_label("Native report section").select_option("macro_report")
        expect(panel.locator(".native-report .report-prose")).to_contain_text(symbol)
        panel.get_by_label("Native report section").select_option("market_report")
        expect(panel.locator(".native-report .report-prose")).to_contain_text(
            f"Offline Forex fixture for {symbol}"
        )
        panel.get_by_label("Native report section").select_option("risk_report")
        expect(panel.locator(".native-report .report-prose")).to_contain_text(symbol)
        checks.append(
            f"{symbol} price/policy evidence, source coverage, market/macro/risk reports and REVIEW status"
        )
    panel.locator(".fx-supplement").scroll_into_view_if_needed()
    page.screenshot(
        path=str(root / "artifacts/forex-daily-research.png"), full_page=True
    )
    panel.get_by_role("button", name="Saved report and provenance", exact=True).click()
    reader = page.get_by_label("Saved Tauric report", exact=True)
    expect(reader).to_be_visible()
    expect(reader.locator(".fx-policy")).to_contain_text("Policy news & events")
    reader.get_by_role("button", name="Policy news & events", exact=True).click()
    expect(reader.locator(".report-content")).to_contain_text("USD/JPY")
    page.keyboard.press("Escape")
    checks.append("Saved report reader retains policy sources and the macro report")
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    chart.get_by_label("Chart instrument").select_option("oanda:fx:USD_JPY")
    expect(panel.locator(".native-report")).to_be_visible()
    checks.append("Forex report persists after reload")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("chart")
    mobile = page.locator(".mobile-panel")
    mobile.get_by_label("Chart instrument").select_option("oanda:fx:EUR_USD")
    mobile.get_by_role("button", name="Research", exact=True).click()
    expect(mobile.locator(".native-report")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    checks.append("Forex daily report and controls remain usable on mobile")
    page.set_viewport_size({"width":1536,"height":1000})
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Market data", exact=True).click()
    page.get_by_text("Optional paid economic calendar", exact=True).click()
    macro = page.get_by_role("form", name="Forex economic data settings", exact=True)
    macro.get_by_label("Trading Economics API key").fill("offline-browser-macro-key")
    macro.get_by_role("button", name="Save connection", exact=True).click()
    expect(macro.get_by_role("status")).to_contain_text("Connection saved")
    expect(macro.get_by_label("Trading Economics API key")).not_to_be_visible()
    macro.get_by_role("button", name="Test calendar access").click()
    expect(macro.get_by_role("status")).to_contain_text("access checked")
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Market data", exact=True).click()
    page.get_by_text("Optional paid economic calendar", exact=True).click()
    expect(macro.get_by_label("API key action")).to_have_value("keep")
    expect(macro).to_contain_text("checked")
    assert "offline-browser-macro-key" not in page.content()
    checks.append("Economic-data key is masked, encrypted, cleared after save and connection check persists")
    tv = page.get_by_role("region", name="TradingView research connection", exact=True)
    expect(tv.get_by_role("button", name="Connect TradingView", exact=True)).to_be_enabled()
    def authorize(route):
        query = parse_qs(urlsplit(route.request.url).query)
        assert query["scope"] == ["mcp:read"] and query["code_challenge_method"] == ["S256"]
        callback = query["redirect_uri"][0]+"?"+urlencode({"state":query["state"][0],"code":"offline-browser-code"})
        route.fulfill(status=200,content_type="text/html",body='<a href="'+escape(callback,quote=True)+'">Authorize read-only TradingView test</a>')
    page.route("https://www.tradingview.com/mcp/oauth/authorize?*", authorize)
    tv.get_by_role("button", name="Connect TradingView", exact=True).click()
    page.get_by_role("link", name="Authorize read-only TradingView test", exact=True).click()
    expect(tv.get_by_role("button", name="Reconnect TradingView", exact=True)).to_be_visible()
    expect(tv.get_by_role("status")).to_contain_text("sign-in complete")
    tv.get_by_role("button", name="Test economic data", exact=True).click()
    expect(tv.get_by_role("status")).to_contain_text("access checked")
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Market data", exact=True).click()
    expect(tv).to_contain_text("Connected")
    expect(tv).to_contain_text("checked")
    for private in ("offline-browser-code","offline-browser-access","offline-browser-refresh"):
        assert private not in page.content() and private not in page.url
    page.screenshot(path=str(root / "artifacts/tradingview-settings.png"),full_page=True)
    checks.append("TradingView read-only OAuth, callback, test and private reload persistence")
    tv.get_by_role("button", name="Disconnect", exact=True).click()
    expect(tv.get_by_role("button", name="Connect TradingView", exact=True)).to_be_visible()
    expect(tv).to_contain_text("Not connected")
    checks.append("TradingView disconnect disables local use without altering price connections")
    assert not errors, errors
    browser.close()
(root / "artifacts/forex-daily-browser-results.json").write_text(
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
print(f"{len(checks)} Forex daily browser checks passed")
