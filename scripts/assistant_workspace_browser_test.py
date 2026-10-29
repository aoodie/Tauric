"""Standalone scan/strategy navigation and review-only assistant receipts."""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Scanners", exact=True).click()
    expect(page.get_by_role("heading", name="Scanners", exact=True)).to_be_visible()
    page.get_by_role("tab", name="My scans", exact=False).click()
    page.get_by_role("button", name="New scanner", exact=True).click()
    form = page.get_by_role("form", name="Scanner profile form")
    expect(form.get_by_label("Scanner name", exact=True)).to_be_visible()
    for section in form.locator("details.workflow-advanced").all():
        assert section.get_attribute("open") is None
    form.get_by_label("Scanner name", exact=True).fill("Simple breakout watch")
    form.get_by_label("Setup scoring", exact=True).select_option("legacy_v1")
    form.get_by_label("Find a saved symbol", exact=True).fill("BTC")
    form.get_by_label("BTC/USDT", exact=True).check()
    form.get_by_label("Find a saved symbol", exact=True).fill("ETH")
    form.get_by_label("ETH/USDT", exact=True).check()
    form.get_by_label("Find a saved symbol", exact=True).fill("")
    expect(form.get_by_label("BTC/USDT", exact=True)).to_be_checked()
    expect(form.get_by_label("ETH/USDT", exact=True)).to_be_checked()
    form.get_by_text("Advanced settings", exact=False).click()
    form.get_by_label("Look back (closed candles)", exact=True).fill("3")
    form.get_by_label("Minimum activity (× average)", exact=True).fill("2")
    expect(form.get_by_label("Tauric research runs per day", exact=True)).to_have_value(
        "0"
    )
    form.get_by_label("Enable this scanner", exact=True).check()
    form.get_by_label("Why save this change?", exact=True).fill(
        "Synthetic browser test scan"
    )
    form.get_by_role("button", name="Save scanner", exact=True).click()
    profile = page.get_by_role("article", name="Simple breakout watch scanner profile")
    expect(profile).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith("/api/v1/scanners/runs")
                              and response.request.method == "POST") as response:
        profile.get_by_role("button", name="Scan now", exact=True).click()
    assert response.value.status == 200
    page.get_by_role("tab", name="Opportunities", exact=False).click()
    expect(page.locator(".scanner-candidate")).to_have_count(2)
    page.get_by_role("button", name="Forex", exact=True).click()
    expect(page.locator(".scanner-candidate")).to_have_count(0)
    page.get_by_role("button", name="All", exact=True).click()
    page.get_by_label("Search opportunities", exact=True).fill("BTC")
    expect(page.locator(".scanner-candidate")).to_have_count(1)
    row = page.locator(".scanner-candidate")
    expect(row.get_by_text("Ready to review", exact=True)).to_be_visible()
    row.get_by_text("Review setup", exact=True).click()
    row.get_by_label("Why take this action?", exact=True).fill(
        "Watch the synthetic setup"
    )
    row.get_by_role("button", name="Watch setup", exact=True).click()
    expect(row.get_by_text("Watching", exact=True)).to_be_visible()
    page.get_by_label("Opportunity status", exact=True).select_option("watch")
    expect(page.locator(".scanner-candidate")).to_have_count(1)
    row.get_by_role("button", name="Dismiss setup", exact=True).click()
    expect(page.locator(".scanner-candidate")).to_have_count(0)
    page.get_by_label("Opportunity status", exact=True).select_option("all")
    expect(
        page.locator(".scanner-candidate").get_by_text("Dismissed", exact=True)
    ).to_be_visible()
    page.get_by_label("Search opportunities", exact=True).fill("")
    page.get_by_label("Opportunity status", exact=True).select_option("active")
    page.screenshot(
        path=str(root / "artifacts/scanners-upgrade-desktop.png"), full_page=True
    )
    checks.append(
        "Create a scan with preserved filtered symbol selections, then scan, filter, watch and dismiss"
    )
    assert "view=scanners" in page.url
    page.reload()
    expect(page.get_by_role("heading", name="Scanners", exact=True)).to_be_visible()
    checks.append("Scanner destination survives reload")

    page.get_by_role("button", name="Strategies", exact=True).click()
    page.get_by_role("button", name="New strategy", exact=True).click()
    form = page.get_by_role("form", name="New strategy version", exact=True)
    for label, value in {
        "Strategy name": "Simple quant baseline",
        "Order size increment": "0.00001",
        "Capital ceiling (account currency)": "1000",
        "Risk per trade (%)": "1",
        "Strategy notes": "Compare simple closed-bar rules with explicit costs and a held-out period.",
        "Why save this version?": "Record the synthetic strategy hypothesis",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_label("Entry and exit rules", exact=True).select_option(
        "closed_bar_breakout_v1"
    )
    expect(form.get_by_text("Breakout rules and exits", exact=False)).to_be_visible()
    assert form.locator("details.workflow-advanced").get_attribute("open") is None
    form.get_by_role("button", name="Save strategy", exact=True).click()
    expect(
        page.get_by_role("article", name="Simple quant baseline versions")
    ).to_be_visible()
    page.get_by_role("tab", name="Strategy library", exact=False).focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Paper routes", exact=False)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_text("No paper routes yet.", exact=False)).to_be_visible()
    page.keyboard.press("ArrowLeft")
    page.screenshot(
        path=str(root / "artifacts/strategies-upgrade-desktop.png"), full_page=True
    )
    page.reload()
    expect(page.get_by_role("heading", name="Strategies", exact=True)).to_be_visible()
    checks.append(
        "Save executable strategy with hidden defaults, navigate library/routes by keyboard, and preserve destination"
    )

    page.get_by_role("button", name="Ask OpenClaw", exact=True).click()
    expect(page.get_by_label("Message OpenClaw", exact=True)).to_have_value(
        re.compile("Review my strategy library")
    )
    page.get_by_text("Project controls & action history", exact=True).click()
    proposal = page.get_by_role("article", name="Synthetic review proposal", exact=True)
    expect(proposal.get_by_text("Review required", exact=True)).to_be_visible()
    expect(
        proposal.get_by_text("Code has not been applied", exact=False)
    ).to_be_visible()
    proposal.get_by_text("Proposed code changes", exact=True).click()
    expect(proposal.locator("pre").first).to_contain_text("+clear label")
    page.get_by_role("button", name="Prepare an upgrade", exact=True).click()
    expect(page.get_by_label("Message OpenClaw", exact=True)).to_have_value(
        re.compile("prepare a concrete code upgrade proposal")
    )
    turns = page.evaluate(
        "async()=>await(await fetch('/api/v1/chat/conversations')).json()"
    )
    assert not turns
    page.screenshot(
        path=str(root / "artifacts/openclaw-upgrade-desktop.png"), full_page=False
    )
    checks.append(
        "Ask shortcuts draft unsent messages; upgrade history shows unapplied, untested proposal and patch"
    )
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="My scans", exact=False).click()
    page.get_by_role("button", name="New scanner", exact=True).click()
    expect(page.get_by_label("Scanner name", exact=True)).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    page.screenshot(
        path=str(root / "artifacts/scanners-upgrade-mobile.png"), full_page=True
    )
    assert not errors, errors
    checks.append("Mobile scanner form fits viewport with no page errors")
    browser.close()

(root / "artifacts/assistant-workspace-browser-results.json").write_text(
    json.dumps(
        {
            "passed": checks,
            "market_source": "synthetic",
            "assistant_proposal": "synthetic_review_fixture",
            "real_model_calls": 0,
            "broker_order_calls": 0,
        },
        indent=2,
    )
)
print(f"{len(checks)} assistant workspace browser checks passed")
