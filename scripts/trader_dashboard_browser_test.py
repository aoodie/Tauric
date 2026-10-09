"""Dashboard, governed controls and activity behavior on explicit offline fixtures."""

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
    page.get_by_role("button", name="Trader dashboard", exact=True).click()
    page.wait_for_load_state("networkidle")
    dashboard = page.locator(".trader-dashboard")
    expect(dashboard.locator(".opportunity-card")).to_have_count(2)
    # Inspect the rendered page before exercising its accessible controls.
    assert "Structural R:R" in dashboard.inner_text()
    assert "OpenClaw activity" in dashboard.inner_text()
    assert "private-credential-fixture" not in dashboard.inner_text()
    card = dashboard.locator(".opportunity-card").first
    expect(card.locator(".factor-grid > div")).to_have_count(6)
    expect(card.get_by_text("Illustrative 2R", exact=True)).to_be_visible()
    state = page.evaluate("async()=>await(await fetch('/api/v1/scanners')).json()")
    assert state["model_calls_during_scan"] == 0
    rating = state["candidates"][0]["assessment"]["rating"]
    dashboard.get_by_label("Setup score filter").select_option(rating)
    expect(dashboard.locator(".opportunity-card")).to_have_count(2)
    dashboard.get_by_label("Setup market filter").select_option("fx")
    expect(
        dashboard.get_by_text("No setups match these filters", exact=True)
    ).to_be_visible()
    dashboard.get_by_label("Setup market filter").select_option("crypto_spot")
    dashboard.get_by_label("Find setup symbol").fill("fixture:crypto:0")
    expect(dashboard.locator(".opportunity-card")).to_have_count(1)
    checks.append(
        "Saved multifactor evidence, rule scores, factor breakdown and combined filters"
    )
    card = dashboard.locator(".opportunity-card").first
    card.get_by_text("Review setup", exact=True).click()
    expect(card.get_by_role("button", name="Watch setup", exact=True)).to_be_disabled()
    card.get_by_label("Why take this action?", exact=True).fill(
        "Watch this synthetic evidence"
    )
    card.get_by_role("button", name="Watch setup", exact=True).click()
    expect(
        dashboard.get_by_text("Setup added to your watching list.", exact=True)
    ).to_be_visible()
    card.get_by_text("Paper plan prerequisites", exact=True).click()
    expect(
        card.get_by_text("Needed · Linked paper strategy", exact=True)
    ).to_be_visible()
    expect(
        card.get_by_role("button", name="Stage paper plan", exact=True)
    ).to_be_disabled()
    checks.append(
        "Watch requires a reason; missing original research and governance block paper staging"
    )
    feed = dashboard.get_by_role("region", name="OpenClaw activity feed")
    expect(feed.locator(".activity-row")).not_to_have_count(0)
    feed.get_by_label("Activity actor").select_option("openclaw")
    feed.get_by_label("Activity status").select_option("queued")
    expect(feed.locator(".activity-row")).not_to_have_count(0)
    row = feed.locator(".activity-row").first
    row.get_by_text("Evidence and outcome", exact=True).click()
    expect(row.get_by_text("Source checksum verified", exact=True)).to_be_visible()
    count = feed.locator(".activity-row").count()
    page.route(
        "**/api/v1/assistant/activities?*",
        lambda route: route.fulfill(
            status=503, json={"detail": "Synthetic activity outage"}
        ),
    )
    feed.get_by_role("button", name="Refresh OpenClaw activity", exact=True).click()
    expect(feed.get_by_role("alert")).to_contain_text(
        "Showing the last successful update"
    )
    expect(feed.locator(".activity-row")).to_have_count(count)
    page.unroute("**/api/v1/assistant/activities?*")
    feed.get_by_role("button", name="Refresh OpenClaw activity", exact=True).click()
    expect(feed.get_by_role("alert")).to_have_count(0)
    checks.append(
        "OpenClaw attribution, queued job state, verified receipts and last-good-data outage recovery"
    )
    page.screenshot(
        path=str(root / "artifacts/trader-dashboard-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(dashboard).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    page.screenshot(
        path=str(root / "artifacts/trader-dashboard-mobile.png"), full_page=True
    )
    checks.append(
        "390px dashboard and feed retain controls without horizontal page overflow"
    )
    page.set_viewport_size({"width": 1536, "height": 1000})
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.locator(".panel-menu > summary").click()
    page.locator(".panel-menu").get_by_role(
        "button", name="OpenClaw activity", exact=True
    ).click()
    expect(
        page.locator(".flexlayout__tab_button").filter(has_text="OpenClaw activity")
    ).to_be_visible()
    expect(page.get_by_role("region", name="OpenClaw activity feed")).to_be_visible()
    checks.append(
        "Activity feed can be added as an independent dockable workspace panel"
    )
    assert not errors, errors
    browser.close()

(root / "artifacts/trader-dashboard-browser-results.json").write_text(
    json.dumps(
        {
            "passed": checks,
            "market_source": "explicit_synthetic_candle_fixture",
            "real_model_calls": 0,
            "broker_order_calls": 0,
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps({"passed": checks}))
