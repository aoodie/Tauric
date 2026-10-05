"""Browser settings checks using an isolated owner; no real keys or model calls."""

import json
import secrets
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
fake_key = "offline-browser-fixture-key"
new_password = secrets.token_urlsafe(24)
results = []


def login(page, password):
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1536, "height": 1000})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture["password"])
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.get_by_role("tab", name="Preferences", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    page.get_by_role("tab", name="Preferences", exact=True).focus()
    page.keyboard.press("ArrowRight")
    expect(
        page.get_by_role("tab", name="AI connections", exact=True)
    ).to_have_attribute("aria-selected", "true")
    expect(
        page.get_by_role("heading", name="Interface density", exact=True)
    ).not_to_be_visible()
    cloud = page.get_by_role("form", name="Direct provider API settings", exact=True)
    expect(
        cloud.get_by_role("button", name="Save connection", exact=True)
    ).to_be_enabled()
    cloud.get_by_label("Quick model", exact=True).fill("offline-quick")
    cloud.get_by_label("Deep model", exact=True).fill("offline-deep")
    cloud.get_by_label("API key action", exact=True).select_option("replace")
    cloud.get_by_label("New API key", exact=True).fill(fake_key)
    cloud.get_by_role("button", name="Save connection", exact=True).click()
    expect(cloud.get_by_role("status")).to_contain_text("Connection saved")
    expect(cloud.locator(".badge")).to_have_text("CONFIGURED")
    expect(cloud.get_by_label("New API key", exact=True)).not_to_be_visible()
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="AI connections", exact=True).click()
    expect(cloud.get_by_label("Quick model", exact=True)).to_have_value("offline-quick")
    expect(cloud.get_by_label("API key action", exact=True)).to_have_value("keep")
    expect(
        cloud.get_by_label("API key action", exact=True).locator("option:checked")
    ).to_have_text("Keep saved key")
    assert fake_key not in page.content()
    results.append(
        "Settings tabs, keyboard switching and encrypted cloud save without returned key"
    )

    second = context.new_page()
    second.goto("http://127.0.0.1:5173")
    expect(second.locator(".app")).to_be_visible()
    second.get_by_role("button", name="Settings", exact=True).click()
    second.get_by_role("tab", name="AI connections", exact=True).click()
    other_cloud = second.get_by_role(
        "form", name="Direct provider API settings", exact=True
    )
    expect(other_cloud.get_by_label("Quick model", exact=True)).to_have_value(
        "offline-quick"
    )
    cloud.get_by_label("Deep model", exact=True).fill("changed-deep")
    cloud.get_by_role("button", name="Save connection", exact=True).click()
    expect(cloud.get_by_role("status")).to_contain_text("Connection saved")
    other_cloud.get_by_label("Quick model", exact=True).fill("stale-edit")
    other_cloud.get_by_role("button", name="Save connection", exact=True).click()
    expect(other_cloud.get_by_role("alert")).to_contain_text("another tab")
    other_cloud.get_by_role("button", name="Reload connection", exact=True).click()
    expect(other_cloud.get_by_label("Deep model", exact=True)).to_have_value(
        "changed-deep"
    )
    results.append("Stale AI connection edits rejected; saved credential kept")

    local = page.get_by_role(
        "form", name="9router / compatible API settings", exact=True
    )
    local.get_by_label("API address", exact=True).fill("http://127.0.0.1:9/v1")
    local.get_by_label("Quick model", exact=True).fill("offline-local")
    local.get_by_label("Deep model", exact=True).fill("offline-local")
    local.get_by_role("button", name="Save connection", exact=True).click()
    expect(local.locator(".badge")).to_have_text("CONFIGURED")
    cloud.get_by_label("API key action", exact=True).select_option("clear")
    cloud.get_by_role("button", name="Save connection", exact=True).click()
    expect(cloud.locator(".badge")).to_have_text("SETUP NEEDED")
    page.screenshot(path=str(root / "artifacts/ai-settings.png"), full_page=True)
    results.append("External local settings and explicit saved-key removal")

    page.get_by_role("tab", name="Security", exact=True).click()
    form = page.get_by_role("form", name="Change password", exact=True)
    form.get_by_label("Current password", exact=True).fill("wrong-current-password")
    form.get_by_label("New password", exact=True).fill(new_password)
    form.get_by_label("Confirm new password", exact=True).fill(
        new_password + "mismatch"
    )
    form.get_by_role("button", name="Change password", exact=True).click()
    expect(form.get_by_role("alert")).to_contain_text("do not match")
    form.get_by_label("Confirm new password", exact=True).fill(new_password)
    form.get_by_role("button", name="Change password", exact=True).click()
    expect(form.get_by_role("alert")).to_contain_text("current password is incorrect")
    form.get_by_label("Current password", exact=True).fill(fixture["password"])
    page.screenshot(path=str(root / "artifacts/security-settings.png"), full_page=True)
    form.get_by_role("button", name="Change password", exact=True).click()
    expect(page.get_by_role("heading", name="Open your workstation")).to_be_visible()
    expect(page.get_by_role("status")).to_contain_text("Password changed")
    second.reload()
    expect(second.get_by_role("heading", name="Open your workstation")).to_be_visible()
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("incorrect")
    page.get_by_label("Password", exact=True).fill(new_password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    results.append(
        "Password validation, all-session revocation, old-password rejection and new sign-in"
    )

    # Restore this isolated owner's initial password for other browser suites.
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Security", exact=True).click()
    form.get_by_label("Current password", exact=True).fill(new_password)
    form.get_by_label("New password", exact=True).fill(fixture["password"])
    form.get_by_label("Confirm new password", exact=True).fill(fixture["password"])
    form.get_by_role("button", name="Change password", exact=True).click()
    expect(page.get_by_role("heading", name="Open your workstation")).to_be_visible()
    login(page, fixture["password"])
    page.get_by_role("button", name="Settings", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="AI connections", exact=True).click()
    expect(local.get_by_label("API address", exact=True)).to_have_value(
        "http://127.0.0.1:9/v1"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(root / "artifacts/ai-settings-mobile.png"), full_page=True)
    page.get_by_role("tab", name="Security", exact=True).click()
    expect(form.get_by_label("Current password", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    results.append("Mobile AI and password controls without horizontal overflow")
    assert not errors, errors
    browser.close()
(root / "artifacts/owner-settings-browser-results.json").write_text(
    json.dumps({"passed": results, "page_errors": errors, "model_calls": 0}, indent=2)
)
print(
    f"Owner settings browser checks passed: {len(results)} workflows. No model calls made."
)
