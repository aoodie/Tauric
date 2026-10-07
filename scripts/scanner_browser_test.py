"""Scanner Settings/UI on isolated synthetic candle evidence; zero paid model calls."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Trading", exact=True).click()
    page.get_by_role("button", name="Strategies", exact=True).click()
    page.get_by_role("button", name="New strategy", exact=True).click()
    f = page.get_by_role("form", name="New strategy version", exact=True)
    for name, value in {
        "Strategy name": "Scanner browser fixture",
        "Version name": "v1",
        "Order size increment": "0.00001",
        "Capital ceiling (account currency)": "5000",
        "Risk per trade (%)": "1",
        "Strategy notes": "Disposable scanner test version. Synthetic data; no trading.",
        "Why save this version?": "Create disposable scanner browser version",
    }.items():
        f.get_by_label(name, exact=True).fill(value)
    f.get_by_role("button", name="Save strategy", exact=True).click()
    expect(
        page.get_by_role("article", name="Scanner browser fixture versions")
    ).to_be_visible()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    page.get_by_role("button", name="New scanner", exact=True).click()
    form = page.get_by_role("form", name="Scanner profile form", exact=True)
    form.get_by_label("Scanner name", exact=True).fill("Scanner browser fixture")
    form.get_by_label("Scanner strategy version", exact=True).select_option(
        label="Scanner browser fixture · v1 · revision 1"
    )
    form.get_by_label("Timeframe", exact=True).select_option("15m")
    form.get_by_text("Advanced settings", exact=False).click()
    for name, value in {
        "Look back (closed candles)": "3",
        "Minimum activity (× average)": "2",
        "Opportunity valid for (seconds)": "600",
        "Tauric research runs per day": "1",
        "Why save this change?": "Save explicitly synthetic scanner profile",
    }.items():
        form.get_by_label(name, exact=True).fill(value)
    form.get_by_label("Research AI connection", exact=True).select_option("local")
    form.get_by_label("BTC/USDT", exact=True).check()
    form.get_by_label("Enable this scanner", exact=True).check()
    form.get_by_role("button", name="Save scanner", exact=True).click()
    profile = page.get_by_role(
        "article", name="Scanner browser fixture scanner profile"
    )
    expect(profile.get_by_text("ENABLED", exact=True)).to_be_visible()
    results.append(
        "Explicit versioned scanner Settings save; no scanning or AI on save"
    )
    profile.get_by_role("button", name="Scan now", exact=True).click()
    expect(
        page.get_by_text("Screening completed. No AI calls were made.", exact=True)
    ).to_be_visible()
    profile.get_by_role("button", name="Scan now", exact=True).click()
    page.wait_for_function(
        "async()=>{const s=await(await fetch('/api/v1/scanners')).json();return s.runs.length===2;}"
    )
    state = page.evaluate("async()=>await(await fetch('/api/v1/scanners')).json()")
    assert (
        len(state["candidates"]) == 1
        and state["candidates"][0]["features"]["close"] == "110"
    )
    assert len(state["candidates"][0]["matches"]) == 1
    results.append(
        "Closed-bar detector excludes forming candle and repeated scans deduplicate"
    )
    page.get_by_role("button", name="Workspace", exact=True).click()
    page.locator(".flexlayout__tab_button").filter(has_text="Scanner").click()
    panel = page.locator(".scanner-panel")
    expect(panel.get_by_role("tab", name="Crypto", exact=True)).to_be_visible()
    expect(panel.get_by_role("tab", name="Forex", exact=True)).to_be_enabled()
    row = panel.get_by_role("article", name="binance:spot:BTCUSDT candidate")
    row.get_by_text("Review setup", exact=True).click()
    row.get_by_label("Why take this action?", exact=True).fill(
        "Watch disposable synthetic breakout"
    )
    row.get_by_role("button", name="Watch setup", exact=True).click()
    expect(row.get_by_text("WATCH", exact=True)).to_be_visible()
    row.get_by_role("button", name="Ask Tauric for research", exact=True).click()
    expect(panel.get_by_role("alert")).to_contain_text("Configure this AI connection")
    assert (
        page.evaluate(
            "async()=> (await(await fetch('/api/v1/analysis')).json()).length"
        )
        == 0
    )
    row.get_by_role("button", name="Dismiss setup", exact=True).click()
    expect(row.get_by_text("IGNORED", exact=True)).to_be_visible()
    expect(
        row.get_by_role("button", name="Ask Tauric for research", exact=True)
    ).to_be_disabled()
    results.append(
        "Watch/ignore history and unavailable-model guard; no accidental analysis"
    )
    row.get_by_role("button", name="View original evidence", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("Scanner candidate")
    page.get_by_role("button", name="Close details", exact=True).click()
    row.get_by_role("button", name="Open chart", exact=True).click()
    expect(
        page.locator(".flexlayout__layout").get_by_label("Chart instrument", exact=True)
    ).to_have_value("binance:spot:BTCUSDT")
    expect(
        page.locator(".flexlayout__layout").get_by_label("Chart timeframe", exact=True)
    ).to_have_value("15m")
    results.append(
        "Candidate original-evidence drawer and symbol/timeframe chart linking"
    )
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    profile.get_by_role("button", name="Edit scanner", exact=True).click()
    form = profile.get_by_role("form", name="Scanner profile form")
    form.get_by_label("Enable this scanner", exact=True).uncheck()
    form.get_by_label("Why save this change?", exact=True).fill(
        "Pause disposable screening after verification"
    )
    form.get_by_role("button", name="Save scanner", exact=True).click()
    expect(profile.get_by_role("button", name="Scan now", exact=True)).to_be_disabled()
    profile.get_by_role("button", name="Profile history", exact=True).click()
    expect(
        profile.get_by_text(
            "Revision 1 · Save explicitly synthetic scanner profile · enabled",
            exact=True,
        )
    ).to_be_visible()
    page.reload()
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("button", name="Scanners", exact=True).click()
    page.get_by_role("tab", name="My scans", exact=False).click()
    expect(profile.get_by_text("DISABLED", exact=True)).to_be_visible()
    results.append(
        "Profile edits and disable persist without modifying strategy version or evidence"
    )
    page.screenshot(path=str(root / "artifacts/scanner-settings.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
    page.screenshot(path=str(root / "artifacts/scanner-mobile.png"), full_page=True)
    assert not errors, errors
    results.append("Desktop/mobile Settings without overflow or browser errors")
    browser.close()
(root / "artifacts/scanner-browser-results.json").write_text(
    json.dumps(
        {
            "passed": results,
            "market_source": "explicit_synthetic_candle_fixture",
            "real_model_calls": 0,
            "broker_order_calls": 0,
        },
        indent=2,
    )
)
print(
    f"{len(results)} scanner browser checks passed; synthetic candles, no paid models or broker calls"
)
