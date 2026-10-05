"""Exercise the real public feed and inactive AI connections on an isolated account."""

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
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:5173")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="New", exact=True).click()
    page.get_by_role("dialog").get_by_label("Workspace name").fill("Market checks")
    page.get_by_role("dialog").get_by_role(
        "button", name="Create workspace", exact=True
    ).click()
    chart = page.locator(".desktop-layout .market-chart-panel")
    expect(
        chart.get_by_role("heading", name="Your market view starts here")
    ).to_be_visible()
    chart.get_by_role("button", name="Refresh market", exact=True).click()
    expect(chart.locator(".market-foot")).to_contain_text("300 candles", timeout=20000)
    expect(chart.locator(".badge")).to_have_text("RECEIVED")
    expect(chart.locator(".market-readout")).to_contain_text("UTC")
    assert chart.locator("canvas").count() >= 2
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(root / "artifacts/market-desktop.png"), full_page=True)
    results.append("Real Binance BTC spot candles, source and receipt freshness")

    chart.get_by_label("SMA", exact=True).check()
    chart.get_by_label("SMA period", exact=True).fill("50")
    canvas = chart.locator(".market-canvas").bounding_box()
    assert canvas and canvas["width"] > 300 and canvas["height"] > 100
    page.mouse.move(
        canvas["x"] + canvas["width"] / 2, canvas["y"] + canvas["height"] / 2
    )
    expect(chart.locator(".market-readout")).to_contain_text("Closed candle")
    chart.get_by_role("button", name="Data details", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Chart data provenance")
    expect(dialog).to_contain_text("observed_at_receipt")
    expect(dialog).to_contain_text("checksum")
    page.keyboard.press("Escape")
    results.append(
        "Crosshair OHLC inspector, editable SMA and immutable provenance drawer"
    )

    chart.get_by_label("Chart timeframe", exact=True).select_option("1h")
    chart.get_by_role("button", name="Refresh market", exact=True).click()
    expect(chart.locator(".market-foot")).to_contain_text("300 candles", timeout=20000)
    chart.get_by_label("Chart instrument", exact=True).select_option(
        "binance:spot:ETHUSDT"
    )
    expect(page.locator(".desktop-layout .native-scope strong")).to_have_text(
        "ETH/USDT"
    )
    chart.get_by_role("button", name="Refresh market", exact=True).click()
    expect(chart.locator(".market-foot")).to_contain_text("300 candles", timeout=20000)
    panel = page.locator(".desktop-layout .native-panel")
    expect(
        panel.get_by_role("button", name="Run daily analysis", exact=True)
    ).to_be_disabled()
    panel.get_by_label("Analysis AI connection", exact=True).select_option("local")
    expect(
        panel.get_by_role("button", name="Run daily analysis", exact=True)
    ).to_be_disabled()
    expect(panel.locator(".native-scope")).to_contain_text("USD")
    results.append(
        "Timeframe and linked ETH context; cloud/local AI stay disabled until configured"
    )

    page.reload()
    expect(page.locator(".app")).to_be_visible()
    expect(chart.locator(".market-foot")).to_contain_text("300 candles")
    results.append("Saved BTC snapshot remains readable after browser reload")

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("chart")
    mobile_chart = page.locator(".mobile-panel .market-chart-panel")
    expect(mobile_chart.get_by_label("Chart instrument", exact=True)).to_be_visible()
    expect(mobile_chart.locator(".market-foot")).to_contain_text("300 candles")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(root / "artifacts/market-mobile.png"), full_page=True)
    page.get_by_label("Active panel", exact=True).select_option("tauric")
    expect(
        page.locator(".mobile-panel").get_by_role("button", name="Run daily analysis")
    ).to_be_disabled()
    results.append("Mobile chart and research panel without horizontal overflow")
    assert not errors, errors
    browser.close()
(root / "artifacts/market-browser-results.json").write_text(
    json.dumps(
        {"passed": results, "page_errors": errors, "feed": "real public Binance REST"},
        indent=2,
    )
)
print(f"Market browser checks passed: {len(results)} workflows. No page errors.")
