"""One-shot demo review in the built UI with disposable fake broker/model facts."""

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
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()

    def open_brokers():
        page.get_by_role("button", name="Paper trading", exact=True).click()
        page.get_by_text("Broker-connected demo trading", exact=True).click()
        page.get_by_label("Demo broker", exact=True).select_option("alpaca")
        return page.get_by_role(
            "region", name="OpenClaw demo entry reviews", exact=True
        )

    section = open_brokers()
    expect(
        section.get_by_role("button", name="Request OpenClaw demo review", exact=True)
    ).to_be_enabled()
    section.get_by_label(
        "Submit demo entry automatically if allowed", exact=True
    ).check()
    lost = []

    def lose_response(route):
        if route.request.method == "POST" and not lost:
            response = route.fetch()
            assert response.status == 201
            lost.append(True)
            route.abort("failed")
        else:
            route.continue_()

    page.route("**/api/v1/brokers/reviews", lose_response)
    section.get_by_role(
        "button", name="Review and submit demo entry if allowed", exact=True
    ).click()
    expect(
        section.get_by_role(
            "button", name="Recover this same OpenClaw review", exact=True
        )
    ).to_be_visible()
    section.get_by_role(
        "button", name="Recover this same OpenClaw review", exact=True
    ).click()
    review = section.get_by_role(
        "article", name="Saved OpenClaw demo review", exact=True
    )
    expect(review).to_have_count(1)
    expect(review.get_by_text("OpenClaw verdict:", exact=False)).to_contain_text(
        "allow", timeout=15000
    )
    expect(review.get_by_text("Linked demo entry:", exact=False)).to_be_visible(
        timeout=15000
    )
    expect(
        review.get_by_text("Buy 200 AAPL · stop 9 · target 18.03", exact=True)
    ).to_be_visible()
    checks.append(
        "Lost review response recovered with one review and one exact demo entry"
    )
    response = page.request.get(base + "/api/v1/brokers")
    assert response.status == 200
    orders = response.json()["orders"]
    assert len(orders) == 1 and orders[0]["status"] == "filled"
    assert orders[0]["request"]["quantity"] == "200"
    history = page.request.get(
        base + f"/api/v1/brokers/orders/{orders[0]['id']}/history"
    ).json()
    assert len([r for r in history if r["kind"] == "submission_started"]) == 1
    checks.append(
        "Actual API receipt links the original native setup and one durable submission"
    )
    section.screenshot(path=str(root / "artifacts/broker-review-desktop.png"))
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".app")).to_be_visible()
    section = open_brokers()
    expect(
        section.get_by_role("article", name="Saved OpenClaw demo review", exact=True)
    ).to_have_count(1)
    expect(section.get_by_text("Linked demo entry:", exact=False)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(
        section.get_by_role("button", name="Refresh OpenClaw demo reviews", exact=True)
    ).to_be_visible()
    section.screenshot(path=str(root / "artifacts/broker-review-mobile.png"))
    assert errors == [], errors
    checks.append(
        "Desktop/mobile controls and saved decision survive reload without browser errors"
    )
    browser.close()
(root / "artifacts/broker-review-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "synthetic_model": True,
            "synthetic_broker": True,
            "connected_orders": 0,
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps(checks))
