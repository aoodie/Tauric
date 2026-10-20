"""Real UI interactions; all research values in this fixture are synthetic."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url = sys.argv[1]
assert url.startswith("http://127.0.0.1:")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Research", exact=True).click()
    settings = page.get_by_role("region", name="Research settings", exact=True)
    settings.get_by_label("Daily AI ceiling · USD", exact=True).fill("2.5")
    settings.get_by_role("button", name="Save research settings", exact=True).click()
    expect(settings.get_by_role("status")).to_contain_text("Research settings saved")
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_role("tab", name="Strategy discovery", exact=True).click()
    view = page.get_by_role("region", name="Strategy discovery", exact=True)
    view.get_by_role("button", name="Data library", exact=True).click()
    view.locator("button.discovery-row").first.click()
    expect(
        view.get_by_role("img", name="Saved bid and ask closes", exact=True)
    ).to_be_visible()
    expect(
        view.get_by_text("Reading this chart made no provider request.", exact=False)
    ).to_be_visible()
    view.locator(".discovery-plot").scroll_into_view_if_needed()
    page.screenshot(
        path=str(root / "artifacts/discovery-history-desktop.png"), full_page=True
    )
    view.get_by_role("button", name="Strategies", exact=True).click()
    view.locator("button.discovery-row").first.click()
    expect(view.get_by_text("When it can fail", exact=True)).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/discovery-strategy-desktop.png"), full_page=True
    )
    view.get_by_role("button", name="Inspect results", exact=True).click()
    expect(view.get_by_role("img", name="Simulated equity", exact=True)).to_be_visible()
    view.get_by_role("button", name="drawdown", exact=True).click()
    expect(view.get_by_role("img", name="Sampled drawdown", exact=True)).to_be_visible()
    view.get_by_role("button", name="price with trades", exact=True).click()
    expect(
        view.get_by_role("img", name="Price and simulated trades", exact=True)
    ).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/discovery-results-desktop.png"), full_page=True
    )
    view.locator(".discovery-plot").screenshot(
        path=str(root / "artifacts/discovery-price-chart.png")
    )
    for width in (420, 320):
        page.set_viewport_size({"width": width, "height": 900})
        expect(
            view.get_by_role("img", name="Price and simulated trades", exact=True)
        ).to_be_visible()
        view.locator(".discovery-plot").scroll_into_view_if_needed()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth+2"
        )
        page.screenshot(
            path=str(root / f"artifacts/discovery-results-{width}.png"), full_page=True
        )
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Research", exact=True).click()
    expect(settings.get_by_label("Daily AI ceiling · USD", exact=True)).to_have_value(
        "2.5"
    )
    assert not errors, errors
    browser.close()
print(
    json.dumps(
        {
            "checks": [
                "Adjustable research budget persists",
                "Saved bid/ask history renders independently",
                "Strategy rules and failure conditions are explained",
                "Equity, drawdown and trade plots are separate",
                "Desktop and 420/320px layouts fit",
            ],
            "browser_errors": errors,
        }
    )
)
