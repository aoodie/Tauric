"""Browser availability/error regression; injected outage, no trading mutations."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1536, "height": 1000})
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Paper trading", exact=True).click()
    expect(page.get_by_text("No open paper positions.", exact=True)).to_be_visible()
    page.route(
        "**/api/v1/paper",
        lambda route: route.fulfill(
            status=503, json={"detail": "Injected storage outage"}
        ),
    )
    page.get_by_role("button", name="Reload paper records", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Injected storage outage")
    expect(page.get_by_text("No open paper positions.", exact=True)).not_to_be_visible()
    page.unroute("**/api/v1/paper")
    page.get_by_role("button", name="Reload paper records", exact=True).click()
    expect(page.get_by_text("No open paper positions.", exact=True)).to_be_visible()
    expect(page.get_by_role("alert")).not_to_be_visible()
    form = page.get_by_role("form", name="Paper proposal", exact=True)
    form.get_by_label("Paper strategy", exact=True).select_option(index=1)
    form.get_by_label("Stop reference (USDT)", exact=True).fill("0")
    form.get_by_label("Proposal reason", exact=True).fill(
        "Invalid stop browser regression"
    )
    form.get_by_role("button", name="Record paper proposal", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("greater than zero")
    # Wait for a successful ordinary poll, then verify it did not erase the action error.
    with page.expect_response(
        lambda r: (
            r.url.endswith("/api/v1/paper")
            and r.request.method == "GET"
            and r.status == 200
        ),
        timeout=10000,
    ):
        pass
    expect(page.get_by_role("alert")).to_contain_text("greater than zero")
    b.close()
(root / "artifacts/paper-state-browser-results.json").write_text(
    json.dumps(
        {
            "passed": [
                "Unavailable storage cannot appear as an empty ledger",
                "Recovery clears only availability error",
                "Validation error survives a normal successful poll",
            ]
        },
        indent=2,
    )
)
print("3 paper availability browser checks passed")
