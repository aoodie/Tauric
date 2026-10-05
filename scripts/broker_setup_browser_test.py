"""Production setup UI against isolated fixtures, never connected demo accounts."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
base_url, version_id = sys.argv[1:3]
if not base_url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable broker setup fixture server.")
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
    page.get_by_role("button", name="Paper trading", exact=True).click()
    page.get_by_text("Broker-connected demo trading", exact=True).click()
    page.get_by_label("Demo broker", exact=True).select_option("alpaca")
    section = page.get_by_role("region", name="Demo setup reviews", exact=True)
    section.screenshot(path=str(root / "artifacts/broker-setup-inspect.png"))
    section.get_by_label("Setup instrument", exact=True).select_option(
        "alpaca:equity:AAPL"
    )
    section.get_by_label("Setup strategy version", exact=True).select_option(version_id)
    section.get_by_label("Setup quantity", exact=True).fill("200")
    section.get_by_label("Setup reason", exact=True).fill(
        "Disposable setup browser verification"
    )
    section.get_by_role("button", name="Check setup evidence", exact=True).click()
    expect(
        section.get_by_text("Buy 200 · stop 9 · target 18.03", exact=True)
    ).to_be_visible()
    expect(
        section.get_by_role("button", name="Request Tauric setup review", exact=True)
    ).to_be_enabled()
    checks.append("Real rule engine, broker quote and exact requested size previewed")
    lost = []

    def lose_first_response(route):
        if route.request.method == "POST" and not lost:
            response = route.fetch()
            assert response.status == 202
            lost.append(True)
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/api/v1/brokers/setup-assessments", lose_first_response)
    section.get_by_role(
        "button", name="Request Tauric setup review", exact=True
    ).click()
    expect(
        section.get_by_role("button", name="Recover this same setup review", exact=True)
    ).to_be_visible()
    section.get_by_role(
        "button", name="Recover this same setup review", exact=True
    ).click()
    expect(section.get_by_text("Setup verdict:", exact=False)).to_contain_text(
        "approve", timeout=15000
    )
    expect(
        section.get_by_role("article", name="Saved setup review", exact=True)
    ).to_have_count(1)
    checks.append(
        "Lost review response recovers the same job without another model invocation"
    )
    section.get_by_role(
        "button", name="Read original Tauric report", exact=True
    ).click()
    reader = section.get_by_role("region", name="Saved Tauric report", exact=True)
    expect(reader.get_by_text("DEMO SETUP REVIEW", exact=True)).to_be_visible()
    expect(
        reader.get_by_text("Research evidence · no trading permission", exact=True)
    ).to_be_visible()
    reader.get_by_role("button", name="Final decision", exact=True).click()
    expect(
        reader.get_by_text("Original synthetic daily BUY report", exact=True)
    ).to_be_visible()
    checks.append(
        "Separate verdict and untouched original daily report displayed without trading authority"
    )
    section.get_by_role("button", name="Close original report", exact=True).click()
    section.screenshot(path=str(root / "artifacts/broker-setup-desktop.png"))
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Paper trading", exact=True).click()
    page.get_by_text("Broker-connected demo trading", exact=True).click()
    section = page.get_by_role("region", name="Demo setup reviews", exact=True)
    expect(
        section.get_by_role("article", name="Saved setup review", exact=True)
    ).to_have_count(1)
    expect(section.get_by_text("Setup verdict:", exact=False)).to_contain_text(
        "approve"
    )
    checks.append("Saved review survives reload")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(
        section.get_by_role("button", name="Refresh setup reviews", exact=True)
    ).to_be_visible()
    section.screenshot(path=str(root / "artifacts/broker-setup-mobile.png"))
    assert errors == [], errors
    checks.append("Desktop/mobile review controls render without browser errors")
    browser.close()
(root / "artifacts/broker-setup-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "synthetic_broker": True,
            "synthetic_model": True,
            "provider_requests": 0,
            "broker_orders": 0,
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps(checks))
