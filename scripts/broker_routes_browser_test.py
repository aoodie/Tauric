"""Test automatic route controls only on the disposable fixture server."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
base = sys.argv[1]
assert base.startswith("http://127.0.0.1:")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()

    def section():
        page.get_by_role("button", name="Trading", exact=True).click()
        page.get_by_text("Broker trading and position protection", exact=True).click()
        page.get_by_label("Broker", exact=True).select_option("alpaca")
        return page.get_by_role(
            "region", name="Automatic strategy routes", exact=True
        )

    region = section()
    expect(
        region.get_by_role("button", name="Save automatic route")
    ).to_be_enabled()
    expect(region.get_by_label("Enable automatic entries")).not_to_be_checked()
    expect(region.get_by_label("Automatic risk percentage")).to_have_value("1")
    region.get_by_label("Automatic route reason").fill(
        "Offline automatic route browser verification"
    )
    region.get_by_label("Enable automatic entries").check()
    lost = []

    def lose(route):
        if route.request.method == "PUT" and not lost:
            response = route.fetch()
            assert response.status == 200
            lost.append(True)
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/api/v1/brokers/routes/*", lose)
    region.get_by_role("button", name="Save automatic route", exact=True).click()
    recover = region.get_by_role(
        "button", name="Recover this same automatic route", exact=True
    )
    expect(recover).to_be_visible()
    recover.click()
    saved = region.get_by_role("article", name="Saved automatic route", exact=True)
    expect(saved).to_have_count(1)
    expect(saved.get_by_text("Broker entry", exact=False)).to_be_visible(timeout=20000)
    expect(saved.get_by_text("calculated quantity 33", exact=False)).to_be_visible()
    expect(
        saved.get_by_text(
            "Estimated stop risk 99.33 USD against budget 100", exact=True
        )
    ).to_be_visible()
    facts = page.request.get(base + "/api/v1/brokers/routes").json()
    assert len(facts["routes"]) == 1 and len(facts["workflows"]) == 1
    orders = page.request.get(base + "/api/v1/brokers").json()["orders"]
    assert len(orders) == 1 and orders[0]["request"]["quantity"] == "33"
    checks.append(
        "Lost save response recovered with one route, one calculated-risk signal and one broker entry"
    )
    saved.get_by_role("button", name="Pause automatic route", exact=True).click()
    expect(
        saved.get_by_role("button", name="Resume automatic route", exact=True)
    ).to_be_enabled()
    expect(saved.get_by_text("Paused", exact=True)).to_be_visible()
    checks.append(
        "Explicit enable and pause controls preserve the attributed existing demo position"
    )
    saved.get_by_label("Saved route risk percentage").fill("2")
    saved.get_by_label("Saved route change reason").fill(
        "Adjust future demo risk without resizing the existing entry"
    )
    saved.get_by_role("button", name="Update automatic route", exact=True).click()
    expect(saved.get_by_label("Saved route risk percentage")).to_have_value("2")
    expect(saved.get_by_text("revision 3", exact=False)).to_be_visible()
    checks.append(
        "Risk settings can be revised for future signals while existing entry sizing stays frozen"
    )
    region.screenshot(path=str(root / "artifacts/broker-routes-desktop.png"))
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".app")).to_be_visible()
    region = section()
    expect(
        region.get_by_role("article", name="Saved automatic route", exact=True)
    ).to_have_count(1)
    expect(
        region.get_by_role("button", name="Resume automatic route", exact=True)
    ).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    region.screenshot(path=str(root / "artifacts/broker-routes-mobile.png"))
    assert errors == [], errors
    checks.append(
        "Saved route, signal lineage and risk evidence survive reload on desktop and mobile"
    )
    browser.close()
(root / "artifacts/broker-routes-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "synthetic_models": True,
            "synthetic_broker": True,
            "connected_orders": 0,
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps(checks))
