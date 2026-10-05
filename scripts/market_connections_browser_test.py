"""Built frontend credential checks against a disposable database only."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
checks = []
secrets = [
    "offline-browser-oanda-token",
    "offline-browser-alpaca-key",
    "offline-browser-alpaca-secret",
]


def settings(page):
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Market data", exact=True).click()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1536, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    settings(page)
    fx = page.get_by_role("form", name="OANDA FX settings", exact=True)
    stock = page.get_by_role("form", name="Alpaca stocks & ETFs settings", exact=True)
    expect(fx.get_by_label("OANDA API token")).to_have_attribute("type", "password")
    fx.get_by_label("OANDA account ID", exact=True).fill("101-001-1234567-001")
    fx.get_by_label("OANDA API token").fill(secrets[0])
    fx.get_by_role("button", name="Save connection", exact=True).click()
    expect(fx.get_by_role("status")).to_contain_text("Connection saved")
    expect(fx.get_by_label("OANDA API token")).not_to_be_visible()
    expect(fx.locator(".badge")).to_have_text("CREDENTIALS SAVED")
    fx.get_by_role("button", name="Test connection", exact=True).click()
    expect(fx.locator(".market-connection-check")).to_contain_text("EUR/USD")
    expect(fx.locator(".market-connection-check")).to_contain_text(
        "Selected OANDA account access checked"
    )
    stock.get_by_label("Alpaca API key").fill(secrets[1])
    stock.get_by_label("Alpaca API secret").fill(secrets[2])
    stock.get_by_label("Market data feed").select_option("sip")
    stock.get_by_label("Data delay · minutes").fill("15")
    stock.get_by_role("button", name="Save connection", exact=True).click()
    expect(stock.get_by_role("status")).to_contain_text("Connection saved")
    stock.get_by_role("button", name="Test connection", exact=True).click()
    expect(stock.locator(".market-connection-check")).to_contain_text("AAPL")
    expect(stock.locator(".market-connection-check")).to_contain_text("sip")
    checks.append("Masked OANDA and Alpaca credentials save in the built UI")
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    settings(page)
    expect(fx.get_by_label("OANDA credential action")).to_have_value("keep")
    expect(fx.get_by_label("OANDA account ID", exact=True)).to_have_value(
        "101-001-1234567-001"
    )
    expect(fx.locator(".market-connection-check")).to_contain_text("EUR/USD")
    expect(stock.get_by_label("Market data feed")).to_have_value("sip")
    assert all(secret not in page.content() for secret in secrets)
    # API responses return flags only; no saved credentials or ciphertext.
    returned = page.evaluate(
        "async () => JSON.stringify(await (await fetch('/api/v1/market-connections')).json())"
    )
    assert all(secret not in returned for secret in secrets)
    assert "encrypted_config" not in returned
    checks.append("Reload keeps saved credentials private and preserves feed selection")
    second = context.new_page()
    second.goto(sys.argv[1])
    expect(second.locator(".app")).to_be_visible()
    settings(second)
    other = second.get_by_role("form", name="Alpaca stocks & ETFs settings", exact=True)
    expect(other.get_by_label("Market data feed")).to_have_value("sip")
    stock.get_by_label("Market data feed").select_option("iex")
    expect(
        stock.get_by_role("button", name="Test connection", exact=True)
    ).to_be_disabled()
    expect(stock).to_contain_text("Save your changes before testing")
    stock.get_by_role("button", name="Save connection", exact=True).click()
    expect(stock.get_by_role("status")).to_contain_text("Connection saved")
    other.get_by_label("Data delay · minutes").fill("0")
    other.get_by_role("button", name="Save connection", exact=True).click()
    expect(other.get_by_role("alert")).to_contain_text("another tab")
    other.get_by_role("button", name="Reload connection", exact=True).click()
    expect(other.get_by_label("Market data feed")).to_have_value("iex")
    checks.append("Feed edits keep credentials; conflicting tab edits require reload")
    page.get_by_role("button", name="Workspace", exact=True).click()
    chart = page.locator(".desktop-layout .market-chart-panel")
    chart.get_by_label("Chart instrument").select_option("oanda:fx:EUR_USD")
    expect(chart).not_to_contain_text("Data credentials needed")
    checks.append("Saved settings update provider readiness in the chart")
    settings(page)
    fx.get_by_label("OANDA credential action").select_option("clear")
    fx.get_by_role("button", name="Save connection", exact=True).click()
    expect(fx.get_by_role("status")).to_contain_text("Credentials cleared")
    expect(fx.locator(".badge")).to_have_text("SETUP NEEDED")
    expect(
        fx.get_by_role("button", name="Test connection", exact=True)
    ).to_be_disabled()
    expect(fx.get_by_label("OANDA account ID", exact=True)).to_have_value("")
    stock.get_by_label("Alpaca environment").select_option("live")
    expect(stock.get_by_label("Alpaca credential action")).to_have_value("replace")
    expect(stock.get_by_label("Alpaca API key")).to_have_value("")
    stock.get_by_label("Alpaca environment").select_option("paper")
    stock.get_by_label("Alpaca credential action").select_option("keep")
    checks.append(
        "Explicit clearing disables new refreshes; environment changes request fresh credentials"
    )
    page.screenshot(path=str(root / "artifacts/market-connections.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(fx.get_by_label("OANDA API token")).to_be_visible()
    expect(stock.get_by_label("Alpaca environment")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(
        path=str(root / "artifacts/market-connections-mobile.png"), full_page=True
    )
    checks.append("Market connection forms fit mobile without horizontal overflow")
    assert not errors, errors
    browser.close()
(root / "artifacts/market-connections-browser-results.json").write_text(
    json.dumps({"checks": checks, "page_errors": errors, "provider_calls": 0}, indent=2)
)
print(f"{len(checks)} credential browser workflows passed; no provider calls made")
