"""Built UI against a disposable offline broker fixture, never primary accounts."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
base_url = sys.argv[1]
if not base_url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable broker browser fixture server.")
checks = []
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
    page.get_by_role("button", name="Trading", exact=True).click()
    readiness = page.get_by_role("region", name="Live trading readiness", exact=True)
    expect(readiness.get_by_text("Live trading inactive", exact=True)).to_be_visible()
    readiness.get_by_role("button", name="Refresh portfolio values", exact=True).click()
    expect(readiness.get_by_role("status")).to_contain_text("Portfolio values refreshed")
    expect(readiness.get_by_role("heading", name="ALPACA paper · paper · USD", exact=True)).to_be_visible()
    expect(readiness).to_contain_text("7")
    expect(readiness.get_by_role("button", name="Activate live trading", exact=True)).to_have_count(0)
    checks.append("Marked broker holdings displayed; unqualified account cannot activate live trading")
    readiness.screenshot(path=str(root / "artifacts/live-readiness-desktop.png"))
    page.get_by_text("Broker trading and position protection", exact=True).click()
    section = page.locator("details").filter(
        has=page.get_by_text("Broker trading and position protection", exact=True)
    )
    page.screenshot(
        path=str(root / "artifacts/broker-browser-inspect.png"), full_page=True
    )
    section.get_by_label("Broker", exact=True).select_option("alpaca")
    expect(
        section.get_by_role("button", name="Enable demo orders", exact=True)
    ).to_be_disabled()
    section.get_by_role("button", name="Check account & positions", exact=True).click()
    expect(section.get_by_role("row", name="AAPL long 7 10", exact=True)).to_be_visible()
    expect(
        section.get_by_role("button", name="Enable demo orders", exact=True)
    ).to_be_disabled()
    section.get_by_label("Use broker limits only, without additional account caps").check()
    section.get_by_role("button", name="Save account limits", exact=True).click()
    expect(
        section.get_by_role("button", name="Enable demo orders", exact=True)
    ).to_be_enabled()
    checks.append("Demo account verification precedes enablement")
    section.get_by_role("button", name="Enable demo orders", exact=True).click()
    expect(
        section.get_by_role("button", name="Pause new entries", exact=True)
    ).to_be_visible()
    section.get_by_label("Instrument", exact=True).fill("AAPL")
    section.get_by_label("Quantity", exact=True).fill("2")
    section.get_by_label("Order type", exact=True).select_option("limit")
    section.get_by_label("Entry price", exact=True).fill("150.00")
    section.get_by_label("Stop loss (optional)", exact=True).fill("140.00")
    section.get_by_label("Take profit (optional)", exact=True).fill("180.00")
    section.get_by_label("Reason", exact=True).fill(
        "Disposable offline broker browser order"
    )
    section.get_by_role("button", name="Submit to Alpaca", exact=True).click()
    expect(section.get_by_role("button", name="Cancel", exact=True)).to_be_enabled()
    expect(
        section.get_by_text(
            "Actual broker order and cumulative fills recorded.", exact=True
        )
    ).to_be_visible()
    checks.append("Typed protected limit order and broker receipt shown")
    section.get_by_role("button", name="Cancel", exact=True).click()
    expect(section.get_by_role("button", name="Cancel", exact=True)).to_be_disabled()
    section.get_by_role("button", name="History", exact=True).click()
    expect(section.locator("pre")).to_contain_text("cancellation_started")
    checks.append("Cancellation and immutable stage history rendered")
    section.get_by_role("button", name="Check position", exact=True).click()
    expect(
        section.get_by_text("open · entered 1 · closed 0 · remaining 1", exact=True)
    ).to_be_visible()
    section.get_by_role("button", name="Restore original protection", exact=True).click()
    expect(section.get_by_text("Protective stop: covered", exact=True)).to_be_visible()
    expect(
        section.get_by_role("button", name="Restore original protection", exact=True)
    ).to_have_count(0)
    checks.append("Original stop and target restored without a fabricated close request")
    section.get_by_role("button", name="Close quantity", exact=True).click()
    section.get_by_label("Close quantity", exact=True).fill("1")
    section.get_by_label("Close reason", exact=True).fill(
        "Disposable attributed position exit"
    )
    section.get_by_role("button", name="Submit position close", exact=True).click()
    expect(
        section.get_by_text("closed · entered 1 · closed 1 · remaining 0", exact=True)
    ).to_be_visible()
    expect(
        section.get_by_text("Attributed gross P&L 10 USD", exact=True)
    ).to_be_visible()
    expect(
        section.get_by_role("button", name="Close quantity", exact=True)
    ).to_be_disabled()
    checks.append("Actual partial entry fill, attributed exit and gross P&L shown")
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Trading", exact=True).click()
    page.get_by_text("Broker trading and position protection", exact=True).click()
    section = page.locator("details").filter(
        has=page.get_by_text("Broker trading and position protection", exact=True)
    )
    section.get_by_label("Broker", exact=True).select_option("alpaca")
    expect(section.get_by_role("button", name="Cancel", exact=True)).to_be_disabled()
    expect(
        section.get_by_text("closed · entered 1 · closed 1 · remaining 0", exact=True)
    ).to_be_visible()
    section.get_by_role("button", name="Pause new entries", exact=True).click()
    expect(
        section.get_by_role("button", name="Enable demo orders", exact=True)
    ).to_be_visible()
    checks.append("Policy/order state survives reload and disablement")
    page.screenshot(
        path=str(root / "artifacts/broker-execution-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    page.get_by_role("region", name="Live trading readiness", exact=True).screenshot(path=str(root / "artifacts/live-readiness-mobile.png"))
    expect(
        section.get_by_role("button", name="Check account & positions", exact=True)
    ).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/broker-execution-mobile.png"), full_page=True
    )
    checks.append("Desktop/mobile controls render without browser errors")
    assert errors == [], errors
    browser.close()
(root / "artifacts/broker-execution-browser-results.json").write_text(
    json.dumps(
        {"checks": checks, "synthetic_broker": True, "provider_requests": 0}, indent=2
    )
    + "\n"
)
print(json.dumps(checks))
