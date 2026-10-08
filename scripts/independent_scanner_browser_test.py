"""Built UI strategy-free discovery on an isolated synthetic-candle server."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
results = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(sys.argv[1])
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    expect(
        page.get_by_text("Choose a saved scan or create one.", exact=False)
    ).to_be_visible()
    state = page.evaluate("async()=>await(await fetch('/api/v1/paper')).json()")
    assert not state["strategy_versions"] and not state["accounts"]
    # Inspect the rendered empty state before following the creation flow.
    assert "New scanner" in page.locator(".scanner-settings").inner_text()

    def create(name, multiple=False):
        page.get_by_role("button", name="New scanner", exact=True).click()
        form = page.get_by_role("form", name="Scanner profile form", exact=True)
        expect(form.locator("select[name=strategy_version_id]")).to_have_value(
            ""
        )
        form.get_by_label("Scanner name", exact=True).fill(name)
        form.get_by_label("Timeframe", exact=True).select_option("15m")
        form.get_by_text("Advanced settings", exact=False).click()
        for label, value in {
            "Minimum setup score (0–1)": "0.50",
            "Minimum research score (0–1)": "0.65",
            "Research shortlist per scan": "2",
            "Scan every (seconds)": "30",
            "Recheck watching list every (seconds)": "10",
            "Wait between repeated setups (seconds)": "0",
            "Look back (closed candles)": "4" if multiple else "3",
            "Minimum activity (× average)": "2",
            "Opportunity valid for (seconds)": "3600",
            "Tauric research runs per day": "1",
            "Why save this change?": "Create independent synthetic scanner",
        }.items():
            form.get_by_label(label, exact=True).fill(value)
        form.get_by_label("Research AI connection", exact=True).select_option("local")
        form.get_by_label("BTC/USDT", exact=True).check()
        form.get_by_label("Enable this scanner", exact=True).check()
        if multiple:
            form.get_by_label("Short breakdown", exact=True).check()
            form.get_by_label("Trend pullback", exact=True).check()
            form.get_by_label(
                "Scan automatically", exact=True
            ).check()
            form.get_by_label(
                "Ask Tauric to research matches automatically", exact=True
            ).check()
        form.get_by_role("button", name="Save scanner", exact=True).click()
        profile = page.get_by_role(
            "article", name=name + " scanner profile", exact=True
        )
        expect(profile.get_by_text("ENABLED", exact=True)).to_be_visible()
        return profile

    first = create("Independent breakout watch")
    expect(
        first.get_by_text(
            "Screening and research only · no strategy linked", exact=True
        )
    ).to_be_visible()
    jobs = page.evaluate("async()=>await(await fetch('/api/v1/analysis')).json()")
    assert not jobs
    results.append("Create named scanner with no strategy, paper account or AI job")
    first.get_by_role("button", name="Scan now", exact=True).click()
    expect(
        page.get_by_text("Screening completed. No AI calls were made.", exact=True)
    ).to_be_visible()
    second = create("Second market watch")
    second.get_by_role("button", name="Scan now", exact=True).click()
    page.wait_for_function(
        "async()=>{const s=await(await fetch('/api/v1/scanners')).json();return s.runs.length===2;}"
    )
    state = page.evaluate("async()=>await(await fetch('/api/v1/scanners')).json()")
    assert len(state["profiles"]) == 2 and len(state["candidates"]) == 1
    assert len(state["candidates"][0]["matches"]) == 2
    assert all(profile["strategy_version_id"] is None for profile in state["profiles"])
    results.append(
        "Multiple standalone profiles share candidate evidence without sharing identity"
    )

    page.get_by_role("button", name="Workspace", exact=True).click()
    page.locator(".flexlayout__tab_button").filter(has_text="Scanner").click()
    row = page.locator(".scanner-panel").get_by_role(
        "article", name="binance:spot:BTCUSDT candidate"
    )
    row.get_by_text("Review setup", exact=True).click()
    dropdown = row.get_by_label("Candidate scanner profile", exact=True)
    expect(dropdown).to_contain_text("Independent breakout watch")
    expect(dropdown).to_contain_text("Second market watch")
    row.get_by_label("Why take this action?", exact=True).fill(
        "Research this synthetic candidate"
    )
    row.get_by_role("button", name="Ask Tauric for research", exact=True).click()
    expect(page.locator(".scanner-panel")).to_contain_text(
        "Daily research requested or reused."
    )
    page.wait_for_function(
        "async()=> (await(await fetch('/api/v1/analysis')).json()).length===1"
    )
    jobs = page.evaluate("async()=>await(await fetch('/api/v1/analysis')).json()")
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["request"]["scanner_context"]["strategy_version_id"] is None
    results.append(
        "Request bounded research from an unlinked profile using fixture model admission"
    )

    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    first.get_by_role("button", name="Edit scanner", exact=True).click()
    form = first.get_by_role("form", name="Scanner profile form")
    form.get_by_label("Scanner name", exact=True).fill("Renamed discovery watch")
    form.get_by_label("Enable this scanner", exact=True).uncheck()
    form.get_by_label("Why save this change?", exact=True).fill(
        "Pause and rename this independent scanner"
    )
    form.get_by_role("button", name="Save scanner", exact=True).click()
    renamed = page.get_by_role(
        "article", name="Renamed discovery watch scanner profile", exact=True
    )
    expect(renamed.get_by_role("button", name="Scan now", exact=True)).to_be_disabled()
    expect(second.get_by_role("button", name="Scan now", exact=True)).to_be_enabled()
    renamed.get_by_role("button", name="Version history", exact=True).click()
    expect(
        renamed.get_by_text(
            "Revision 1 · Create independent synthetic scanner · enabled", exact=True
        )
    ).to_be_visible()
    results.append(
        "Rename and disable one scanner while preserving independent history and the other scanner"
    )
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    expect(renamed.get_by_text("DISABLED", exact=True)).to_be_visible()
    expect(second.get_by_text("ENABLED", exact=True)).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/independent-scanner-desktop.png"), full_page=True
    )
    results.append("Profiles and optional links survive page reload")
    multiple = create("Automatic multi-setup watch", multiple=True)
    expect(multiple.get_by_text("SCHEDULED SCREENING", exact=False)).to_be_visible()
    expect(
        multiple.get_by_text(
            "Long breakout / Short breakdown / Trend pullback", exact=False
        )
    ).to_be_visible()
    state = page.evaluate("async()=>await(await fetch('/api/v1/scanners')).json()")
    parameters = next(
        p["parameters"]
        for p in state["profiles"]
        if p["name"] == "Automatic multi-setup watch"
    )
    assert parameters["automatic_enabled"] and parameters["automatic_research_enabled"]
    assert parameters["detector_ids"] == [
        "closed_bar_breakout_v1",
        "closed_bar_breakdown_v1",
        "trend_pullback_v1",
    ]
    expect(
        page.get_by_text(
            "Automatic research is waiting for its coordinator.", exact=True
        )
    ).to_be_visible()
    results.append(
        "Multiple setup selection and automatic research controls persist with coordinator status"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="New scanner", exact=True).click()
    expect(page.get_by_label("Scanner name", exact=True)).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    page.screenshot(
        path=str(root / "artifacts/independent-scanner-mobile.png"), full_page=True
    )
    assert not errors, errors
    results.append(
        "Desktop and mobile creation form renders without overflow or page errors"
    )
    browser.close()

(root / "artifacts/independent-scanner-browser-results.json").write_text(
    json.dumps(
        {
            "passed": results,
            "market_source": "explicit_synthetic_candle_fixture",
            "model_transport": "configured_fixture_only_no_worker",
            "real_model_calls": 0,
            "broker_order_calls": 0,
        },
        indent=2,
    )
)
print(f"{len(results)} standalone scanner browser checks passed")
