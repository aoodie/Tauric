"""Exercise real built forms and saved evidence; native responses are offline fixtures."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url, first, second, strategy, dataset = sys.argv[1:]
if not url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable local fixture server.")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="Research & analytics", exact=True).click()
    page.get_by_role("tab", name="Strategy qualification", exact=True).click()
    page.wait_for_load_state("networkidle")
    if not page.get_by_label("Qualification portfolio", exact=True).count():
        print(page.locator("body").inner_text())
        print(errors)
        page.screenshot(
            path=str(root / "artifacts/qualification-debug.png"), full_page=False
        )
    page.get_by_label("Qualification portfolio", exact=True).select_option(first)
    expect(page.get_by_label("Minimum net return (%)", exact=True)).to_have_value("")
    page.get_by_label("Saved executable version", exact=True).select_option(strategy)
    page.get_by_label("Qualification dataset", exact=True).select_option(dataset)
    page.get_by_label("Hypothesis", exact=True).fill(
        "Synthetic test of frozen breakout windows and execution costs"
    )
    for name, value in {
        "Training bars": "7",
        "Embargo bars": "0",
        "Minimum holdout bars": "7",
        "Minimum closed trades": "1",
        "Minimum net return (%)": "0",
        "Maximum drawdown (%)": "10",
        "Experiment starting balance (dataset quote currency)": "1000",
        "Experiment allocation (%)": "50",
        "Experiment risk per trade (%)": "1",
        "Baseline fee (bps)": "10",
        "Baseline slippage (bps)": "5",
        "Stress fee (bps)": "30",
        "Stress slippage (bps)": "20",
        "Assessment validity (hours)": "24",
    }.items():
        page.get_by_label(name, exact=True).fill(value)
    page.get_by_label("Registration reason", exact=True).fill(
        "Verify synthetic research evidence; no real performance claim"
    )
    page.get_by_role("button", name="Freeze protocol", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Protocol frozen")
    checks.append("Owner-entered thresholds, costs and split frozen before evaluation")
    page.get_by_role("button", name="Evaluate frozen protocol", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Separate training")
    expect(page.get_by_role("table").first).to_contain_text("Higher execution costs")
    expect(
        page.get_by_role("button", name="Evaluate frozen protocol", exact=True)
    ).to_be_disabled()
    page.get_by_role("button", name="Record qualification report", exact=True).click()
    expect(
        page.get_by_role(
            "heading", name="Report 1 · incomplete · research scope", exact=True
        )
    ).to_be_visible()
    checks.append(
        "Separate costed windows; repeated evaluation disabled; missing native assessment incomplete"
    )
    page.get_by_label("Assessment connection", exact=True).select_option("local")
    page.get_by_role(
        "button", name="Request exact-version Tauric assessment", exact=True
    ).click()
    expect(
        page.get_by_role("button", name="Inspect original Tauric evidence", exact=True)
    ).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Record qualification report", exact=True).click()
    expect(
        page.get_by_role(
            "heading", name="Report 2 · passed · research scope", exact=True
        )
    ).to_be_visible()
    expect(
        page.get_by_role(
            "heading", name="Report 1 · incomplete · research scope", exact=True
        )
    ).to_be_visible()
    page.get_by_role(
        "button", name="Inspect original Tauric evidence", exact=True
    ).click()
    expect(page.get_by_role("dialog").locator(".report-verdict")).to_contain_text(
        "Strategy verdict"
    )
    page.get_by_role("dialog").get_by_role(
        "button", name="Final decision", exact=True
    ).click()
    expect(page.get_by_role("dialog")).to_contain_text(
        "Original daily fixture decision"
    )
    expect(page.get_by_role("dialog")).to_contain_text("strategy_assessment")
    page.get_by_role("button", name="Close details", exact=True).click()
    checks.append(
        "Offline exact-version native verdict; original reports preserved; earlier report retained"
    )
    page.get_by_label("Qualification portfolio", exact=True).select_option(second)
    expect(
        page.get_by_text("No protocols registered for this portfolio.", exact=True)
    ).to_be_visible()
    expect(page.locator(".qualification-report")).to_have_count(0)
    page.get_by_label("Qualification portfolio", exact=True).select_option(first)
    expect(
        page.get_by_role(
            "heading", name="Report 2 · passed · research scope", exact=True
        )
    ).to_be_visible()
    checks.append("Portfolio isolation and persisted evidence reload")
    page.get_by_role(
        "heading", name="Strategy qualification", exact=True
    ).scroll_into_view_if_needed()
    page.screenshot(
        path=str(root / "artifacts/qualification-desktop.png"), full_page=False
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    page.screenshot(
        path=str(root / "artifacts/qualification-mobile.png"), full_page=False
    )
    checks.append("Desktop/mobile layout and no browser errors")
    assert not errors, errors
    browser.close()
(root / "artifacts/qualification-browser-results.json").write_text(
    json.dumps(
        {"offline_fixture": True, "connected_model_verified": False, "checks": checks},
        indent=2,
    )
    + "\n"
)
print(f"{len(checks)} qualification browser checks passed (offline native fixture)")
