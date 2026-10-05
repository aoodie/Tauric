"""Owner allocation editing/history/conflicts and responsive UI on a fixture account."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
results = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.get_by_role("button", name="Add capital pot", exact=True).click()
    form = page.get_by_role("form", name="New capital allocation", exact=True)
    form.get_by_label("Parent allocation", exact=True).select_option(
        label="Browser portfolio / Browser capital"
    )
    for label, value in {
        "Allocation name": "Browser nested",
        "Allocation budget (USDT)": "3000",
        "Allocation total risk (%)": "1",
        "Allocation maximum positions": "2",
        "Allocation setup reason": "Browser nested capital test",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_role("button", name="Create allocation", exact=True).click()
    expect(
        page.get_by_role(
            "heading",
            name="Browser portfolio / Browser capital / Browser nested",
            exact=True,
        )
    ).to_be_visible()
    results.append("Nested allocation created under a child pot")
    card = page.get_by_role("article", name="Browser nested allocation", exact=True)
    card.get_by_role("button", name="Edit allocation", exact=True).click()
    form = card.get_by_role("form", name="Browser nested allocation limits", exact=True)
    form.get_by_label("Allocation budget (USDT)", exact=True).fill("2500")
    form.get_by_label("Allocation change reason", exact=True).fill(
        "Browser budget revision"
    )
    form.get_by_role("button", name="Save allocation", exact=True).click()
    expect(card.locator(".badge")).to_have_text("v2")
    card.get_by_role("button", name="Allocation history", exact=True).click()
    history = card.get_by_label("Browser nested allocation history", exact=True)
    expect(history.get_by_text("Version 1", exact=True)).to_be_visible()
    expect(history.get_by_text("Version 2", exact=True)).to_be_visible()
    expect(history).to_contain_text("3000 USDT")
    expect(history).to_contain_text("2500 USDT")
    results.append("Budget edits append versions and history preserves the original")
    conflict = page.evaluate("""async () => {
      const s = await (await fetch('/api/v1/session')).json();
      const state = await (await fetch('/api/v1/paper')).json();
      const scope = state.capital_scopes.find(v => v.name === 'Browser nested');
      const r = await fetch(`/api/v1/paper/capital/${scope.id}`, {method:'PUT', headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token}, body:JSON.stringify({expected_revision:1,limits:scope.limits,reason:'Browser stale edit'})});
      return r.status;
    }""")
    assert conflict == 409
    results.append("An older browser revision cannot overwrite the saved budget")
    # Attempt a sibling that exceeds the remaining parent allocation.
    page.get_by_role("button", name="Add capital pot", exact=True).click()
    form = page.get_by_role("form", name="New capital allocation", exact=True)
    form.get_by_label("Parent allocation", exact=True).select_option(
        label="Browser portfolio / Browser capital"
    )
    for label, value in {
        "Allocation name": "Browser oversubscribed",
        "Allocation budget (USDT)": "8000",
        "Allocation total risk (%)": "1",
        "Allocation maximum positions": "2",
        "Allocation setup reason": "Browser rejected budget test",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_role("button", name="Create allocation", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("exceed a parent budget")
    expect(
        page.get_by_role("heading", name="Browser oversubscribed", exact=True)
    ).not_to_be_visible()
    form.get_by_role("button", name="Cancel", exact=True).click()
    results.append(
        "Oversubscribed child budget reports a visible error and creates no allocation"
    )
    page.screenshot(path=str(root / "artifacts/capital-settings.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(
        card.get_by_role("button", name="Edit allocation", exact=True)
    ).to_be_visible()
    overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    assert not overflow
    page.screenshot(path=str(root / "artifacts/capital-mobile.png"), full_page=True)
    assert not errors, errors
    results.append(
        "Mobile allocation controls and history fit without horizontal overflow"
    )
    browser.close()
(root / "artifacts/capital-browser-results.json").write_text(
    json.dumps({"passed": results}, indent=2)
)
print(f"{len(results)} capital browser checks passed")
