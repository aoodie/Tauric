"""Offline Lab UI interaction against the test-owned API and synthetic history."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url = sys.argv[1]
assert url.startswith("http://127.0.0.1:")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_role("tab", name="Research Lab", exact=True).click()
    lab = page.get_by_role("region", name="Research Lab", exact=True)
    expect(lab.get_by_role("heading", name="Research Lab", exact=True)).to_be_visible()
    expect(lab.get_by_text("Worker offline", exact=True)).to_have_count(2)
    selector = lab.get_by_label("Saved dataset", exact=True)
    selector.select_option(label="Frozen Forex history · fx · 1m · 360 bars")
    lab.get_by_label("Study name", exact=True).fill("London-compatible evidence study")
    lab.get_by_label("Session preset", exact=True).select_option("London")
    expect(lab.get_by_label("Session time zone", exact=True)).to_have_value(
        "Europe/London"
    )
    expect(lab.get_by_label("Opening", exact=True)).to_have_value("08:00")
    lab.get_by_label("Session preset", exact=True).select_option("All trading hours")
    lab.get_by_role("button", name="Save session report", exact=True).click()
    expect(lab.get_by_role("status")).to_contain_text("Session report saved")
    study = lab.locator(".lab-history details.research-block").first
    study.locator("summary").first.click()
    expect(study).to_contain_text("Upside breakout")
    expect(study).to_contain_text("Record checksum")
    with page.expect_download() as download:
        study.get_by_role("button", name="Export record", exact=True).click()
    exported = json.loads(Path(download.value.path()).read_text())
    assert exported["payload"]["authority"] == "research_only"
    assert exported["payload"]["bars"] == 360
    checks.append(
        "Session presets, report save, scenarios and checksum export use the real API"
    )
    lab.get_by_label("Study", exact=True).select_option("qlib")
    lab.get_by_label("Study name", exact=True).fill("Held-out model experiment")
    expect(
        lab.get_by_label("FX daily financing debit · bps (negative = credit)")
    ).to_be_visible()
    lab.get_by_role("button", name="Queue experiment", exact=True).click()
    expect(lab.get_by_role("status")).to_contain_text("Experiment queued")
    job = lab.locator(".lab-history details.research-block").first
    job.locator("summary").first.click()
    job.get_by_role("button", name="Cancel experiment", exact=True).click()
    expect(job.locator("summary").first).to_contain_text("cancelled")
    expect(
        job.get_by_role("button", name="Cancel experiment", exact=True)
    ).to_have_count(0)
    checks.append("Model queue exposes worker availability and preserves cancellation")
    page.screenshot(
        path=str(root / "artifacts/research-lab-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 420, "height": 900})
    expect(lab.get_by_role("heading", name="Research Lab", exact=True)).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/research-lab-mobile.png"), full_page=True
    )
    assert not errors, errors
    checks.append("Desktop and mobile screen render without browser errors")
    browser.close()
(root / "artifacts/research-lab-browser.json").write_text(
    json.dumps({"checks": checks, "browser_errors": errors}, indent=2)
)
print(json.dumps({"passed": len(checks), "checks": checks}))
