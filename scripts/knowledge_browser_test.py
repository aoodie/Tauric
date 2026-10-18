"""Exercise the private knowledge flow on an isolated synthetic owner database."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    errors = []
    external_images = []
    page.on(
        "request",
        lambda r: (
            external_images.append(r.url) if "memory-image.invalid" in r.url else None
        ),
    )
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Knowledge", exact=True).click()
    expect(page.get_by_role("heading", name="Knowledge", exact=True)).to_be_visible()
    page.get_by_role("button", name="New note", exact=True).click()
    form = page.get_by_role("form", name="Knowledge note editor")
    form.get_by_label("Note title", exact=True).fill("Breakout research hypothesis")
    form.get_by_label("Note text", exact=True).fill(
        "Compare breakout entries against a simple baseline after spread costs. Check a held-out period before qualification.\n\n![Private reference](https://memory-image.invalid/probe.png)"
    )
    form.get_by_role("button", name="Save note", exact=True).click()
    reader = page.get_by_role("article", name="Selected knowledge note")
    expect(
        reader.get_by_role("heading", name="Breakout research hypothesis")
    ).to_be_visible()
    expect(reader.get_by_text("automatic loading is off", exact=False)).to_be_visible()
    assert not external_images
    checks.append(
        "Markdown image references do not send automatic requests outside the local library"
    )
    expect(
        reader.get_by_role("button", name="Discuss with OpenClaw", exact=True)
    ).to_be_disabled()
    expect(reader).to_contain_text("Saved memory stays local")
    page.get_by_label("Search your knowledge", exact=True).fill("breakout baseline")
    expect(page.locator(".knowledge-result")).to_have_count(1)
    reader.get_by_role("button", name="Edit note", exact=True).click()
    page.screenshot(
        path=str(root / "artifacts/knowledge-edit-check.png"), full_page=True
    )
    (root / "artifacts/knowledge-edit-check.txt").write_text(
        page.locator("body").inner_text()
    )
    form = page.get_by_role("form", name="Knowledge note editor")
    form.get_by_label("Note text", exact=True).fill(
        "Compare breakout entries against a simple baseline after spread, fees and slippage. Validate forward before qualification."
    )
    form.get_by_role("button", name="Save note", exact=True).click()
    expect(reader).to_contain_text("fees and slippage")
    reader.get_by_role("button", name="Hide from recall", exact=True).click()
    expect(page.locator(".knowledge-result")).to_have_count(0)
    page.get_by_label("Include hidden notes", exact=True).check()
    expect(page.locator(".knowledge-result")).to_have_count(1)
    reader.get_by_role("button", name="Restore to recall", exact=True).click()
    expect(
        reader.get_by_role("button", name="Hide from recall", exact=True)
    ).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/knowledge-library-desktop.png"), full_page=True
    )
    checks.append(
        "Create, search, edit, hide and restore a local research note; remote discussion stays blocked"
    )
    page.get_by_role("tab", name="Your preferences", exact=False).click()
    page.get_by_role("button", name="Add preference", exact=True).click()
    form = page.get_by_role("form", name="Knowledge note editor")
    form.get_by_label("Note title", exact=True).fill("My explanation preference")
    form.get_by_label("Note text", exact=True).fill(
        "Explain hypotheses and counter-evidence in clear language."
    )
    form.get_by_role("button", name="Save note", exact=True).click()
    expect(page.locator(".knowledge-result")).to_have_count(1)
    expect(
        page.get_by_text("No learned preferences are available yet.", exact=False)
    ).to_be_visible()
    checks.append(
        "Explicit preferences stay separate from asynchronous inferred patterns"
    )
    page.get_by_role("tab", name="Memory controls", exact=True).click()
    form = page.get_by_role("form", name="Memory controls")
    expect(
        form.get_by_label("Allow memory in remote OpenClaw chat", exact=False)
    ).not_to_be_checked()
    form.get_by_label("Record and sync local memory", exact=False).check()
    form.get_by_role("button", name="Save memory controls", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("Memory controls saved.")
    expect(
        page.get_by_role("heading", name="Memory stays local", exact=True)
    ).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/knowledge-controls-desktop.png"), full_page=True
    )
    checks.append(
        "Save recording controls while retaining private remote-model recall policy"
    )
    page.reload()
    expect(page.get_by_role("heading", name="Knowledge", exact=True)).to_be_visible()
    assert "view=knowledge" in page.url
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="Memory controls", exact=True).click()
    expect(
        page.get_by_role("button", name="Save memory controls", exact=True)
    ).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(root / "artifacts/knowledge-mobile.png"), full_page=True)
    checks.append(
        "Knowledge destination survives reload and controls fit a narrow screen"
    )
    assert not page.evaluate(
        "async()=>await(await fetch('/api/v1/chat/conversations')).json()"
    )
    assert not errors, errors
    assert not external_images
    browser.close()
    (root / "artifacts/knowledge-browser-verification.json").write_text(
        json.dumps(
            {
                "fixture": "isolated synthetic owner database; no remote inference or broker actions",
                "checks": checks,
                "page_errors": errors,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"checks": checks, "page_errors": errors}))
