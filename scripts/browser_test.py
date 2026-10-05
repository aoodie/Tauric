"""Native Playwright checks against isolated test accounts, never real brokers."""

import json
import subprocess
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
screenshots = root / "artifacts"
results = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1536, "height": 1000})
    page = context.new_page()
    console_errors = []
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Open your workstation")).to_be_visible()
    page.screenshot(path=str(screenshots / "login.png"), full_page=True)
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_text("Saved · v1", exact=True)).to_be_visible()
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(screenshots / "workstation.png"), full_page=True)
    print("Rendered tabs:", page.get_by_role("tab").all_text_contents())
    print(
        "Rendered layout buttons:",
        page.locator(".flexlayout__layout button").evaluate_all(
            '(elements) => elements.map(x => ({label:x.getAttribute("aria-label"),title:x.title,text:x.textContent}))'
        ),
    )
    results.append("Owner login and honest empty workstation")

    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    row = page.locator(".setting-row").filter(
        has=page.get_by_role("heading", name="Interface density", exact=True)
    )
    row.get_by_label("Interface density", exact=True).select_option("terminal")
    row.get_by_label("Interface density reason").fill(
        "Use compact terminal spacing for testing"
    )
    row.get_by_role("button", name="Save change", exact=True).click()
    expect(row.get_by_text("From", exact=False)).to_contain_text("user · v1")
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    expect(page.locator(".app")).to_have_class("app density-terminal ")
    page.get_by_role("button", name="Settings", exact=True).click()
    row = page.locator(".setting-row").filter(
        has=page.get_by_role("heading", name="Interface density", exact=True)
    )
    row.get_by_label("Interface density", exact=True).select_option("comfortable")
    row.get_by_label("Interface density reason").fill("Compare comfortable spacing")
    row.get_by_role("button", name="Save change", exact=True).click()
    expect(row.get_by_text("From", exact=False)).to_contain_text("user · v2")
    row.get_by_role("button", name="History", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(
        dialog.get_by_text("Compare comfortable spacing", exact=True)
    ).to_be_visible()
    dialog.get_by_label("Restoration reason").fill("Restore terminal spacing")
    dialog.locator(".history-list article").filter(
        has=page.get_by_text("v1", exact=True)
    ).get_by_role("button", name="Restore", exact=True).click()
    expect(page.get_by_role("dialog")).not_to_be_visible()
    expect(row.get_by_text("From", exact=False)).to_contain_text("user · v3")
    page.screenshot(path=str(screenshots / "settings.png"), full_page=True)
    results.append("Settings persistence, revision history and restoration")

    # Two tabs must not overwrite each other's settings.
    second = context.new_page()
    second.goto("http://127.0.0.1:5173")
    expect(second.locator(".app")).to_be_visible()
    second.get_by_role("button", name="Settings", exact=True).click()
    second_row = second.locator(".setting-row").filter(
        has=second.get_by_role("heading", name="Interface density", exact=True)
    )
    # Establish that both tabs actually captured v3 before advancing the first.
    expect(second_row.get_by_text("From", exact=False)).to_contain_text("user · v3")
    row.get_by_label("Interface density reason").fill("First tab changes the setting")
    row.get_by_label("Interface density", exact=True).select_option("compact")
    row.get_by_role("button", name="Save change", exact=True).click()
    expect(row.get_by_text("From", exact=False)).to_contain_text("user · v4")
    second_row.get_by_label("Interface density reason").fill(
        "Second tab tries a stale change"
    )
    second_row.get_by_label("Interface density", exact=True).select_option(
        "comfortable"
    )
    second_row.get_by_role("button", name="Save change", exact=True).click()
    expect(second_row.get_by_role("alert")).to_contain_text("changed in another tab")
    second_row.get_by_role("button", name="Reload current value", exact=True).click()
    expect(second_row.get_by_text("From", exact=False)).to_contain_text("user · v4")
    second.close()
    results.append("Conflicting edits are rejected in the browser")

    page.get_by_role("button", name="Workspace", exact=True).click()
    # Real docking and splitter interaction using the inspected rendered layout.
    scanner = page.get_by_role("tab", name="Scanner", exact=False)
    if scanner.count() == 0:
        scanner = page.locator(".flexlayout__tab_button").filter(has_text="Scanner")
    scanner.click()
    expect(
        page.get_by_role("heading", name="No scanner is running", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("Unsaved layout", exact=True)).to_be_visible()
    page.get_by_role("button", name="Save layout", exact=True).click()
    expect(page.get_by_text("Saved · v2", exact=True)).to_be_visible()
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    expect(
        page.get_by_role("heading", name="No scanner is running", exact=True)
    ).to_be_visible()
    results.append("Selected panel and saved layout survive reload")

    # Collapse and expand the active scanner panel, then restore a closed panel.
    page.get_by_role("button", name="Collapse Scanner", exact=True).click()
    expect(page.get_by_text("Panel collapsed", exact=True)).to_be_visible()
    page.get_by_role("button", name="Expand panel", exact=True).click()
    expect(
        page.get_by_role("heading", name="No scanner is running", exact=True)
    ).to_be_visible()
    scanner_tab = page.locator(".flexlayout__tab_button").filter(has_text="Scanner")
    scanner_tab.locator('[title*="Close"], [aria-label*="Close"]').first.click()
    page.locator(".panel-menu summary").click()
    page.locator(".panel-menu").get_by_role(
        "button", name="Scanner", exact=True
    ).click()
    expect(
        page.get_by_role("heading", name="No scanner is running", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Save layout", exact=True).click()
    expect(page.get_by_text("Saved · v3", exact=True)).to_be_visible()
    results.append("Collapse, expand, close and restore panels")

    # Drag a tab to another group.
    source = (
        page.locator(".flexlayout__tab_button")
        .filter(has_text="Scanner")
        .bounding_box()
    )
    target = (
        page.locator(".flexlayout__tab_button")
        .filter(has_text="Tauric analysis")
        .bounding_box()
    )
    assert source and target
    page.mouse.move(
        source["x"] + source["width"] / 2, source["y"] + source["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        target["x"] + target["width"] / 2, target["y"] + target["height"] / 2, steps=30
    )
    page.mouse.up()
    expect(page.get_by_text("Unsaved layout", exact=True)).to_be_visible()
    page.get_by_role("button", name="Save layout", exact=True).click()
    expect(page.get_by_text("Saved · v4", exact=True)).to_be_visible()

    def saved_layout():
        workspace_id = page.get_by_label("Workspace", exact=True).input_value()
        return page.evaluate(
            "async (id) => (await (await fetch('/api/v1/workspaces/'+id)).json()).layout",
            workspace_id,
        )

    moved_layout = saved_layout()

    def parents(node, result=None):
        if result is None:
            result = {}
        for child in node.get("children", []):
            if child.get("type") == "tab":
                result[child["component"]] = node["id"]
            else:
                parents(child, result)
        return result

    groups = parents(moved_layout["layout"])
    assert groups["scanner"] == groups["tauric"], (
        "Drag did not move Scanner to the analysis dock"
    )
    # Resize a visible divider.
    splitter = page.locator(".flexlayout__splitter").first.bounding_box()
    assert splitter
    page.mouse.move(
        splitter["x"] + splitter["width"] / 2, splitter["y"] + splitter["height"] / 2
    )
    page.mouse.down()
    page.mouse.move(
        splitter["x"]
        + splitter["width"] / 2
        + (55 if splitter["width"] < splitter["height"] else 0),
        splitter["y"]
        + splitter["height"] / 2
        + (55 if splitter["width"] >= splitter["height"] else 0),
        steps=15,
    )
    page.mouse.up()
    page.get_by_role("button", name="Save layout", exact=True).click()
    expect(page.get_by_text("Saved · v5", exact=True)).to_be_visible()
    assert saved_layout() != moved_layout, "Divider did not change saved proportions"
    page.get_by_role("button", name="Maximize tabset", exact=True).first.click()
    expect(
        page.get_by_role("button", name="Restore tabset", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Restore tabset", exact=True).click()
    results.append("Real tab drag, divider resize and maximize/restore")

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Clone", exact=True).click()
    page.get_by_role("dialog").get_by_label("Workspace name").fill("Research copy")
    page.get_by_role("dialog").get_by_role(
        "button", name="Create workspace", exact=True
    ).click()
    expect(
        page.get_by_label("Workspace", exact=True).locator("option:checked")
    ).to_have_text("Research copy")
    page.get_by_role("button", name="Reset", exact=True).click()
    expect(page.get_by_text("Saved · v2", exact=True)).to_be_visible()
    page.get_by_role("button", name="Layout history", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.locator(".history-list article").filter(
        has=page.get_by_text("v1", exact=True)
    ).get_by_role("button", name="Restore", exact=True).click()
    expect(page.get_by_text("Saved · v3", exact=True)).to_be_visible()
    results.append("Workspace clone, reset and history restoration")

    page.get_by_role("button", name="Activity", exact=True).click()
    page.locator(".audit-row").first.click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="Full resource view", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_class("drawer drawer-wide")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).not_to_be_visible()
    results.append("Resource drawer, full view and keyboard dismissal")

    compose = [
        "docker",
        "compose",
        "--env-file",
        str(root / ".env"),
        "-f",
        str(root / "deploy/compose.dev.yml"),
    ]
    subprocess.run([*compose, "stop", "db"], check=True, capture_output=True)
    try:
        expect(page.get_by_text("Storage unavailable", exact=True)).to_be_visible(
            timeout=25000
        )
        page.get_by_role("button", name="Settings", exact=True).click()
        row = page.locator(".setting-row").filter(
            has=page.get_by_role("heading", name="Interface density", exact=True)
        )
        row.get_by_label("Interface density reason").fill(
            "Unavailable storage must reject this change"
        )
        row.get_by_label("Interface density", exact=True).select_option("terminal")
        row.get_by_role("button", name="Save change", exact=True).click()
        expect(row.get_by_role("alert")).to_contain_text(
            "Storage is unavailable", timeout=15000
        )
    finally:
        subprocess.run([*compose, "start", "db"], check=True, capture_output=True)
    expect(page.get_by_text("Storage ready", exact=True)).to_be_visible(timeout=25000)
    subprocess.run([*compose, "restart", "api"], check=True, capture_output=True)
    page.reload()
    expect(page.get_by_text("Saved · v5", exact=True)).to_be_visible()
    expect(page.locator(".app")).to_have_class("app density-compact ")
    page.get_by_label("Workspace", exact=True).select_option(label="Research copy")
    expect(page.get_by_text("Saved · v3", exact=True)).to_be_visible()
    results.append(
        "Database outage is visible; restart preserves sessions, settings and workspaces"
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Toggle navigation", exact=True).click()
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.get_by_role("button", name="Toggle navigation", exact=True).click()
    expect(page.get_by_label("Active panel", exact=True)).to_be_visible()
    page.get_by_label("Active panel", exact=True).select_option("openclaw")
    expect(page.get_by_text("Paper decision review", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")
    results.append("Narrow screen panels and keyboard focus")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.get_by_role("heading", name="Open your workstation")).to_be_visible()
    results.append("Sign out revokes owner access")
    assert not console_errors, console_errors
    browser.close()

(screenshots / "browser-results.json").write_text(
    json.dumps({"passed": results, "console_errors": console_errors}, indent=2)
)
print(f"Browser checks passed: {len(results)} workflows. No page errors.")
