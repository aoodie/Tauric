"""Actual owner task controls on an isolated account; no model or broker calls."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
(root / "artifacts").mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="OpenClaw", exact=True).click()
    page.wait_for_load_state("networkidle")
    panel = page.locator(".project-tasks:visible")
    expect(panel).to_be_visible()
    panel.locator("summary").first.click()
    goal = panel.get_by_role("article", name="Browser project goal", exact=True)
    child = panel.get_by_role("article", name="Browser dependent task", exact=True)
    expect(goal.get_by_text("Ready to continue", exact=True)).to_be_visible()
    goal.get_by_role("button", name="Pause work", exact=True).click()
    expect(goal.get_by_text("Paused", exact=True)).to_be_visible()
    expect(child.get_by_text("Paused", exact=True)).to_be_visible()
    goal.get_by_role("button", name="Resume work", exact=True).click()
    expect(goal.get_by_text("Ready to continue", exact=True)).to_be_visible()
    expect(child.get_by_text("Ready to continue", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(goal.get_by_role("button", name="Cancel work", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    goal.get_by_role("button", name="Cancel work", exact=True).click()
    expect(goal.get_by_text("Cancelled", exact=True)).to_be_visible()
    expect(child.get_by_text("Cancelled", exact=True)).to_be_visible()
    expect(goal.get_by_role("button", name="Resume work")).to_have_count(0)
    page.screenshot(
        path=str(root / "artifacts/project-tasks-mobile.png"), full_page=True
    )
    assert not errors, errors
    browser.close()
(root / "artifacts/project-tasks-browser-results.json").write_text(
    json.dumps(
        {
            "disposable_account": True,
            "model_calls": 0,
            "broker_order_calls": 0,
            "checks": [
                "Saved parent and child visible",
                "Pause propagates",
                "Resume restores inherited work",
                "Cancellation stays terminal",
                "390px controls remain visible without overflow",
            ],
        },
        indent=2,
    )
    + "\n"
)
print(
    "Project task owner controls passed on the built frontend and real disposable database."
)
