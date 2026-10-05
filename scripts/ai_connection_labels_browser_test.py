"""Saved router/direct API bindings, model tiers and mobile copy; no model calls."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url = sys.argv[1]
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1139, "height": 763})
    errors, model_requests = [], []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.on(
        "request",
        lambda req: (
            model_requests.append(req.url)
            if req.method == "POST" and "/api/v1/analysis" in req.url
            else None
        ),
    )
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="AI connections", exact=True).click()
    router = page.get_by_role(
        "form", name="9router / compatible API settings", exact=True
    )
    direct = page.get_by_role("form", name="Direct provider API settings", exact=True)
    expect(router.get_by_label("Provider", exact=True)).to_have_value(
        "openai_compatible"
    )
    expect(router).to_contain_text("model names from its dashboard")
    expect(direct).to_contain_text("Connect directly to OpenAI")
    assert (
        "Local review model"
        not in page.locator("#settings-section-models").inner_text()
    )
    expect(page.get_by_label("9router review model", exact=True)).to_have_value("deep")
    expect(page.get_by_label("Direct API review model", exact=True)).to_have_value(
        "deep"
    )
    checks.append(
        "API connection names, 9router provider option and review labels match the owner's setup"
    )
    for form, quick, deep in [
        (router, "fixture/router-quick", "fixture/router-deep"),
        (direct, "fixture-direct-quick", "fixture-direct-deep"),
    ]:
        if form == router:
            form.get_by_label("API address", exact=True).fill("http://127.0.0.1:9/v1")
        form.get_by_label("Quick model", exact=True).fill(quick)
        form.get_by_label("Deep model", exact=True).fill(deep)
        form.get_by_label("API key action", exact=True).select_option("replace")
        form.get_by_label("New API key", exact=True).fill("offline-fixture-api-key")
        form.get_by_role("button", name="Save connection", exact=True).click()
        expect(form.get_by_role("status")).to_contain_text("Connection saved")
    page.get_by_label("9router review model", exact=True).select_option("quick")
    page.get_by_label("9router review model reason", exact=True).fill(
        "Verify saved router quick model binding"
    )
    row = page.locator(".setting-row").filter(
        has=page.get_by_role("heading", name="9router review model", exact=True)
    )
    row.get_by_role("button", name="Save change", exact=True).click()
    expect(row.locator(".inheritance")).to_contain_text("Effective quick")
    values = page.evaluate(
        "async () => await (await fetch('/api/v1/model-connections')).json()"
    )
    assert {v["id"]: v["quick_model"] for v in values} == {
        "local": "fixture/router-quick",
        "cloud": "fixture-direct-quick",
    }
    assert all(v["has_key"] and v["configured"] for v in values)
    assert "offline-fixture-api-key" not in page.content()
    checks.append(
        "Router and direct API save to their existing bindings; keys stay private; quick review model selection is saved"
    )
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="AI connections", exact=True).click()
    expect(router.get_by_label("Quick model", exact=True)).to_have_value(
        "fixture/router-quick"
    )
    expect(direct.get_by_label("Deep model", exact=True)).to_have_value(
        "fixture-direct-deep"
    )
    expect(page.get_by_label("9router review model", exact=True)).to_have_value("quick")
    page.screenshot(path=str(root / "artifacts/ai-api-connections-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    expect(router).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(root / "artifacts/ai-api-connections-mobile.png"))
    checks.append(
        "Saved API connections and review model choices survive reload and fit mobile"
    )
    page.set_viewport_size({"width": 1139, "height": 763})
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("tauric")
    expect(
        page.locator(".mobile-panel")
        .get_by_label("Analysis AI connection")
        .locator("option[value=local]")
    ).to_have_text("9router / compatible API")
    expect(
        page.locator(".mobile-panel")
        .get_by_label("Analysis AI connection")
        .locator("option[value=cloud]")
    ).to_have_text("Direct provider API")
    checks.append("Research uses the same API connection names")
    assert not errors, errors
    assert not model_requests, model_requests
    browser.close()
(root / "artifacts/ai-api-connections-browser-results.json").write_text(
    json.dumps(
        {
            "checks": checks,
            "model_calls": 0,
            "provider_calls": 0,
            "fixture_credentials_only": True,
        },
        indent=2,
    )
)
print(f"{len(checks)} API connection browser checks passed")
