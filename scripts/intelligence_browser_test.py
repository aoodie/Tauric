"""Verify actual rendered market views without connecting models or brokers."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url = sys.argv[1]
assert url.startswith("http://127.0.0.1:")
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator(".app")).to_be_visible()
    page.get_by_role("button", name="Market intelligence", exact=True).click()
    expect(page.locator(".mi-heatmap")).to_be_visible()
    page.screenshot(path=str(root / "artifacts/intelligence-initial.png"))
    (root / "artifacts/intelligence-rendered-controls.json").write_text(
        json.dumps(page.locator(".mi-page button").all_text_contents(), indent=2)
    )
    markets = page.get_by_role("navigation", name="Market intelligence markets")
    markets.get_by_role("button", name="Stocks & ETFs", exact=True).click()
    expect(page.get_by_role("button", name="Select AAPL", exact=True)).to_be_visible()
    page.get_by_role("button", name="Select AAPL", exact=True).click()
    panel = page.get_by_role("complementary").get_by_role(
        "region", name="Selected market instrument"
    )
    expect(panel.get_by_role("heading", name="AAPL", exact=True)).to_be_visible()
    page.get_by_role("button", name="Watch AAPL", exact=True).click()
    expect(
        page.get_by_role("button", name="Unwatch AAPL", exact=True)
    ).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="All selected symbols", exact=True).click()
    expect(page.locator(".mi-table tbody tr")).to_have_count(1)
    page.reload()
    expect(page.get_by_role("button", name="Unwatch AAPL", exact=True)).to_be_visible()
    expect(
        markets.get_by_role("button", name="Stocks & ETFs", exact=True)
    ).to_have_attribute("aria-current", "page")
    checks.append("Stock market tab and watchlist persist across reload")
    page.get_by_label("Search research symbols", exact=True).fill("apple")
    page.locator(".mi-search").get_by_role("button", name="Search", exact=True).click()
    expect(page.locator(".mi-table tbody tr")).to_have_count(1)
    page.get_by_role("button", name="Refresh prices & sources", exact=True).click()
    expect(page.locator(".mi-notice")).to_contain_text("Refresh complete")
    expect(page.locator(".mi-news-item").first).to_contain_text(
        "Company reports results"
    )
    page.locator(".mi-news-item").first.get_by_role(
        "button", name="Source receipt", exact=True
    ).click()
    dialog = page.get_by_role("dialog", name="Market source receipt")
    expect(
        dialog.get_by_role("heading", name="Alpaca news", exact=True)
    ).to_be_visible()
    dialog.get_by_label("Close source receipt").click()
    page.locator(".mi-page").evaluate("el => el.scrollTop = 0")
    page.screenshot(path=str(root / "artifacts/intelligence-stocks-desktop.png"))
    checks.append(
        "Daily prices, source receipts, news and search work in production assets"
    )
    panel.get_by_role("button", name="Open daily chart", exact=True).click()
    expect(
        page.locator(".desktop-layout .market-chart-panel").get_by_label(
            "Chart instrument"
        )
    ).to_have_value("alpaca:equity:AAPL")
    checks.append("Chart handoff selects the exact stock instrument")
    page.get_by_role("button", name="Market intelligence", exact=True).click()
    expect(
        page.get_by_role("button", name="Ask OpenClaw about AAPL", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Ask OpenClaw about AAPL", exact=True).click()
    expect(page.locator(".chat-compose textarea")).to_contain_text(
        "Explain the current research context for AAPL"
    )
    checks.append("Chat opens an editable evidence-bound draft without sending it")
    page.get_by_role("button", name="Market intelligence", exact=True).click()
    markets.get_by_role("button", name="Forex", exact=True).click()
    page.get_by_role("button", name="Refresh prices & sources", exact=True).click()
    expect(page.locator(".mi-notice")).to_contain_text("Refresh complete")
    expect(
        page.get_by_role("heading", name="Currency pair performance")
    ).to_be_visible()
    expect(
        page.locator(".mi-news-item").filter(has_text="date only").first
    ).to_be_visible()
    page.locator(".mi-page").evaluate("el => el.scrollTop = 0")
    page.screenshot(path=str(root / "artifacts/intelligence-forex-desktop.png"))
    checks.append("Forex displays currency pairs and sourced date-only policy meetings")
    for name, text in (
        ("Commodities", "Gold, oil"),
        ("Rates & bonds", "yield observations"),
    ):
        markets.get_by_role("button", name=name, exact=True).click()
        expect(page.locator(".mi-coverage-gap")).to_contain_text(text)
    checks.append("Unsupported markets expose dedicated coverage gaps")
    markets.get_by_role("button", name="Stocks & ETFs", exact=True).click()
    expect(page.locator(".mi-table")).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".mi-heatmap")).to_be_visible()
    page.locator(".mi-page").evaluate("el => el.scrollTop = 0")
    page.screenshot(path=str(root / "artifacts/intelligence-mobile.png"))
    overflow = page.evaluate(
        """() => ({width:innerWidth, total:document.documentElement.scrollWidth, elements:[...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth+1 && getComputedStyle(el).position !== 'absolute').map(el => ({tag:el.tagName, classes:el.className, right:el.getBoundingClientRect().right, width:el.getBoundingClientRect().width, scroll:el.scrollWidth})).slice(0,20)})"""
    )
    if overflow["total"] > overflow["width"] + 1:
        overflow["containers"] = page.evaluate(
            """() => [...document.querySelectorAll('html, body, .app, .app-main, .mi-page, .mi-grid, .mi-main, .mi-section, .mi-table-scroll, .mi-markets, .mi-coverage, dialog, .app-footer')].map(el => ({classes:el.className, tag:el.tagName, width:el.getBoundingClientRect().width, left:el.getBoundingClientRect().left, right:el.getBoundingClientRect().right, client:el.clientWidth, scroll:el.scrollWidth, overflow:getComputedStyle(el).overflow, position:getComputedStyle(el).position}))"""
        )
    assert overflow["total"] <= overflow["width"] + 1, overflow
    checks.append(
        "390px mobile layout contains horizontal overflow within the table and tabs"
    )
    assert not errors, errors
    browser.close()
(root / "artifacts/intelligence-browser-results.json").write_text(
    json.dumps({"checks": checks, "console_errors": errors}, indent=2) + "\n"
)
print(json.dumps({"checks": len(checks), "console_errors": errors}))
