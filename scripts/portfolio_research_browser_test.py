"""Daily research form, saved evidence and responsive layout, against the disposable fixture."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url, first, second, profile = sys.argv[1:]
if not url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable local test server.")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_label("Conversation context", exact=True).select_option(first)
    page.get_by_text("Daily opportunity and strategy research", exact=True).click()
    panel = page.locator(".portfolio-research")
    expect(panel).to_contain_text("Coordinator ready")
    page.get_by_text(
        "Shared daily AI job allowance · all portfolios", exact=True
    ).click()
    page.get_by_label("Shared jobs per UTC day", exact=True).fill("0")
    page.get_by_role("button", name="Save shared allowance", exact=True).click()
    expect(panel).to_contain_text("Shared allowance saved.")
    panel.get_by_label("Automatic daily research", exact=True).check()
    page.get_by_label("Research daily time", exact=True).fill("08:30")
    page.get_by_label("Research timezone", exact=True).fill("Europe/London")
    panel.get_by_label("FX · data connection needed", exact=True).check()
    panel.get_by_label(
        "Stocks, including small caps · data connection needed", exact=True
    ).check()
    panel.get_by_label("BTCUSDT", exact=True).check()
    panel.locator("fieldset").filter(
        has=page.get_by_text("Opportunity scanner profiles", exact=True)
    ).locator("input[type=checkbox]").first.check()
    page.get_by_role("button", name="Add experiment", exact=True).click()
    page.get_by_label("Experiment 1 hypothesis", exact=True).fill(
        "Compare costed momentum on the saved offline dataset"
    )
    panel.get_by_label("Fast average bars", exact=True).fill("1")
    panel.get_by_label("Slow average bars", exact=True).fill("3")
    panel.get_by_label("Compare neighbouring fast average windows", exact=True).check()
    page.get_by_role("button", name="Save research settings", exact=True).click()
    expect(panel).to_contain_text("Research settings saved.")
    page.get_by_role("button", name="Run portfolio research now", exact=True).click()
    run = panel.locator(".portfolio-research-runs > details").first
    expect(run.locator("summary").first).to_contain_text("partial", timeout=20000)
    run.locator("summary").first.click()
    expect(run).to_contain_text("Digest saved")
    expect(run).to_contain_text("Replay")
    expect(run.get_by_role("button", name="Open replay evidence")).to_have_count(2)
    run.get_by_role("button", name="Open replay evidence").first.click()
    expect(page.get_by_role("dialog")).to_contain_text(
        "Compare costed momentum on the saved offline dataset"
    )
    page.get_by_role("button", name="Close details", exact=True).click()
    checks.append(
        "Both tracks with real saved replays and deferred FX/stocks; no native admission"
    )
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_label("Conversation context", exact=True).select_option(second)
    page.get_by_text("Daily opportunity and strategy research", exact=True).click()
    expect(page.locator(".portfolio-research")).to_contain_text("revision 0")
    expect(page.locator(".portfolio-research-runs > details")).to_have_count(0)
    checks.append("Portfolio research isolation")
    page.get_by_label("Conversation context", exact=True).select_option(first)
    page.get_by_text("Daily opportunity and strategy research", exact=True).click()
    expect(page.get_by_label("Research daily time", exact=True)).to_have_value("08:30")
    page.get_by_role(
        "button", name="View research settings history", exact=True
    ).click()
    expect(page.locator(".portfolio-research")).to_contain_text("Research revision 1")
    checks.append("Versioned policy and reload persistence")
    page.locator(".chat-page").evaluate("node => node.scrollTo(0, 0)")
    page.screenshot(
        path=str(root / "artifacts/portfolio-research-desktop.png"), full_page=False
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    page.screenshot(
        path=str(root / "artifacts/portfolio-research-mobile.png"), full_page=False
    )
    checks.append("Desktop and mobile layout")
    assert not errors, errors
    browser.close()
(root / "artifacts/portfolio-research-browser-results.json").write_text(
    json.dumps({"offline_fixture": True, "checks": checks}, indent=2) + "\n"
)
print(f"{len(checks)} daily portfolio research browser checks passed (offline feeds)")
