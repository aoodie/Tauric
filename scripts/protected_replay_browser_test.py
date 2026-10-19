"""Exercise protected research replay on a disposable owner; no broker/model calls."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_label("Saved dataset", exact=True).select_option(
        label="Synthetic protected stock history · 1m · 7 candles"
    )
    page.get_by_label("Replay rules", exact=True).select_option("closed_bar_breakout_v1")
    page.get_by_label("Saved strategy version", exact=True).select_option(index=1)
    page.get_by_label("Execution model", exact=True).select_option("protected")
    page.get_by_label("Assumed price increment", exact=True).fill("1")
    page.get_by_label("Full spread · basis points", exact=True).fill("0")
    page.get_by_label("Experiment name", exact=True).fill("Browser protected replay")
    page.get_by_role("button", name="Run replay", exact=True).click()
    evidence = page.get_by_label("Replay execution evidence", exact=True)
    expect(evidence).to_be_visible()
    expect(evidence).to_contain_text("Ambiguous exit candles")
    with page.expect_download() as download:
        page.get_by_role("button", name="Export result", exact=True).click()
    record = json.loads(Path(download.value.path()).read_text())
    result = record["payload"]["result"]["execution_evidence"]
    assert result["broker_parity_verified"] is False
    assert len(result["ambiguous_exits"]) == 1
    # The signed-in workstation polls live data. Wait for the relevant UI
    # instead of requiring global network silence after restoring the session.
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_role("button", name="Browser protected replay", exact=False).click()
    expect(page.get_by_label("Replay execution evidence", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    evidence.scroll_into_view_if_needed()
    box = evidence.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    (ROOT / "artifacts").mkdir(exist_ok=True)
    page.screenshot(path=str(ROOT / "artifacts/protected-replay-mobile.png"), full_page=True)
    assert errors == [], errors
    browser.close()
print("Protected replay controls, saved ambiguity, export, reload and mobile layout passed.")
