"""Readable report, library, original export and mobile checks on offline fixtures."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url, artifact_id = sys.argv[1:]
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1139, "height": 763})
    errors, posts = [], []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.on(
        "request",
        lambda req: (
            posts.append(req.url)
            if req.method == "POST" and "/api/v1/analysis" in req.url
            else None
        ),
    )
    page.goto(url + "#resource/report/" + artifact_id)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.goto(url + "#resource/report/" + artifact_id)
    dialog = page.get_by_role("dialog", name="Tauric research report")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".report-content table")).to_contain_text("$82,437")
    expect(dialog.locator(".report-content strong")).to_have_text("Hold")
    expect(dialog.locator(".report-details pre")).not_to_be_visible()
    assert dialog.locator(".resource-facts").count() == 0
    assert dialog.locator(".report-prose script, .report-prose img").count() == 0
    assert not page.evaluate("Boolean(window.reportInjected)")
    assert not dialog.get_by_role("link", name="Unsafe link").get_attribute("href")
    checks.append(
        "Readable headings, emphasis, lists and tables; raw data collapsed; unsafe report content inert"
    )
    dialog.get_by_role("button", name="Market analysis", exact=True).click()
    expect(dialog.locator(".report-content")).to_contain_text(
        "Original market narrative preserved"
    )
    dialog.get_by_role("button", name="Final decision", exact=True).click()
    page.screenshot(path=str(root / "artifacts/report-reader-desktop.png"))
    with page.expect_download() as downloaded:
        dialog.get_by_role("button", name="Download original", exact=True).click()
    original = json.loads(Path(downloaded.value.path()).read_text())
    assert original["id"] == artifact_id
    assert "<script>" in original["payload"]["reports"]["final_trade_decision"]
    checks.append("Section navigation and exact original export")
    page.get_by_role("button", name="Close details", exact=True).click()
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_role("tab", name="Tauric reports", exact=True).click()
    library = page.get_by_role("region", name="Tauric report library")
    expect(library.locator(".report-content table")).to_be_visible()
    expect(library.locator(".report-history-list > button")).to_have_count(30)
    library.get_by_role("button", name="Load older research", exact=True).click()
    expect(library.locator(".report-history-list > button")).to_have_count(32)
    library.locator(".report-history-list > button").nth(1).click()
    expect(library.locator(".report-verdict")).to_contain_text("Strategy verdict")
    expect(library.locator(".report-verdict")).to_contain_text("incomplete")
    expect(library.locator(".report-content")).to_contain_text(
        "Forward execution evidence is missing"
    )
    library.locator(".report-history-list > button").nth(2).click()
    expect(library.locator(".report-history-view")).to_contain_text(
        "Offline fixture failure"
    )
    expect(library.locator(".report-reader")).to_have_count(0)
    library.locator(".report-history-list > button").first.click()
    expect(library.locator(".report-content table")).to_be_visible()
    page.screenshot(path=str(root / "artifacts/report-library-desktop.png"))
    checks.append(
        "Saved report library, older history, exact strategy verdict and failed run without stale report"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(library.locator(".report-content table")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(root / "artifacts/report-library-mobile.png"))
    library.get_by_role("button", name="Open report details", exact=True).click()
    expect(dialog.locator(".report-content table")).to_be_visible()
    assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
    page.screenshot(path=str(root / "artifacts/report-reader-mobile.png"))
    checks.append("Report reader and research library fit mobile viewport")
    assert not errors, errors
    assert not posts, posts
    browser.close()
(root / "artifacts/report-reader-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "model_calls": 0,
            "provider_calls": 0,
            "synthetic_reports": True,
        },
        indent=2,
    )
)
print(f"{len(checks)} report reader browser checks passed")
