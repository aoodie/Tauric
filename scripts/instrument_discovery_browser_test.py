"""Discovery, explicit classification and saved universes; disposable offline server only."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url, portfolio = sys.argv[1:]
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
    chart = page.locator(".desktop-layout .market-chart-panel")
    expect(chart).to_be_visible()
    discovery = chart.locator(".instrument-discovery")
    discovery.locator("summary").first.click()
    expect(discovery.get_by_label("Discovery market")).to_be_visible()
    discovery.get_by_label("Discovery market").select_option("alpaca")
    discovery.get_by_label("Find provider symbol").fill("nvidia")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    row = discovery.get_by_role("article", name="Discovered NVDA", exact=True)
    expect(row.get_by_role("button", name="Add symbol")).to_be_disabled()
    row.get_by_label("Asset type for NVDA").select_option("equity")
    row.get_by_role("button", name="Add symbol").click()
    expect(discovery.get_by_role("status")).to_contain_text("NVDA added")
    expect(chart.get_by_label("Chart instrument")).to_have_value("alpaca:equity:NVDA")
    expect(row).to_contain_text("Saved · Stock")
    checks.append(
        "Provider name search requires explicit stock classification and adds the selected chart symbol"
    )
    discovery.get_by_label("Find provider symbol").fill("QQQ")
    discovery.get_by_label("Find provider symbol").press("Enter")
    row = discovery.get_by_role("article", name="Discovered QQQ", exact=True)
    row.get_by_label("Asset type for QQQ").select_option("etf")
    row.get_by_role("button", name="Add symbol").click()
    expect(discovery.get_by_role("status")).to_contain_text("QQQ added")
    discovery.get_by_label("Discovery market").select_option("oanda")
    discovery.get_by_label("Find provider symbol").fill("EUR/GBP")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    discovery.get_by_role("article", name="Discovered EUR_GBP", exact=True).get_by_role(
        "button", name="Add symbol"
    ).click()
    expect(discovery.get_by_role("status")).to_contain_text("EUR/GBP added")
    discovery.get_by_label("Find provider symbol").fill("AUD_CAD")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    expect(
        discovery.get_by_role("article", name="Discovered AUD_CAD", exact=True)
    ).to_contain_text("daily AI research pending")
    checks.append(
        "ETF classification and additional FX crosses work; unsupported daily FX context is disclosed"
    )
    page.reload()
    expect(page.locator(".app")).to_be_visible()
    chart.get_by_label("Chart instrument").select_option("alpaca:equity:NVDA")
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Scanners", exact=True).click()
    page.get_by_role("button", name="Add scanner profile", exact=True).click()
    form = page.get_by_role("form", name="Scanner profile form", exact=True)
    nested = form.locator(".instrument-discovery")
    nested.locator("summary").first.click()
    nested.get_by_label("Discovery market").select_option("alpaca")
    nested.get_by_label("Find provider symbol").fill("NVDA")
    nested.get_by_label("Find provider symbol").press("Enter")
    expect(
        nested.get_by_role("article", name="Discovered NVDA", exact=True)
    ).to_contain_text("Saved · Stock")
    expect(
        form.get_by_role("button", name="Save scanner profile", exact=True)
    ).to_be_visible()
    nested.locator("summary").first.click()
    form.get_by_label("Scanner name", exact=True).fill("Discovered stock universe")
    form.get_by_label("Candle interval", exact=True).select_option("15m")
    for label, value in {
        "Prior closed candles": "3",
        "Minimum activity ratio": "2",
        "Candidate lifetime (seconds)": "600",
        "New research runs per UTC day": "0",
        "Scanner change reason": "Save discovered stock for research",
    }.items():
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_label("Research AI connection", exact=True).select_option("local")
    form.get_by_label("NVDA", exact=True).check()
    form.get_by_role("button", name="Save scanner profile", exact=True).click()
    expect(
        page.get_by_role(
            "article", name="Discovered stock universe scanner profile", exact=True
        )
    ).to_contain_text("NVDA")
    checks.append(
        "New registry selection persists in an independent scanner without starting a scan"
    )
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_label("Conversation context", exact=True).select_option(portfolio)
    page.get_by_text("Daily opportunity and strategy research", exact=True).click()
    panel = page.locator(".portfolio-research")
    panel.get_by_label("Stocks, including small caps", exact=True).check()
    panel.get_by_label("ETFs", exact=True).check()
    panel.get_by_label("NVDA", exact=True).check()
    panel.get_by_label("QQQ", exact=True).check()
    panel.get_by_role("button", name="Save research settings", exact=True).click()
    expect(panel).to_contain_text("Research settings saved.")
    page.reload()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    expect(page.get_by_label("Conversation context", exact=True)).to_have_value(
        portfolio
    )
    page.get_by_text("Daily opportunity and strategy research", exact=True).click()
    expect(
        page.locator(".portfolio-research").get_by_label("NVDA", exact=True)
    ).to_be_checked()
    expect(
        page.locator(".portfolio-research").get_by_label("QQQ", exact=True)
    ).to_be_checked()
    expect(
        page.locator(".portfolio-research").get_by_label(
            "Automatic daily research", exact=True
        )
    ).not_to_be_checked()
    checks.append(
        "Portfolio universe includes new stock and ETF after reload; automation stays disabled"
    )
    page.get_by_role("button", name="Workspace", exact=True).click()
    discovery = chart.locator(".instrument-discovery")
    discovery.locator("summary").first.click()
    discovery.get_by_label("Discovery market").select_option("alpaca")
    discovery.get_by_label("Find provider symbol").fill("BLOCKED")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    expect(
        discovery.get_by_role(
            "article", name="Discovered BLOCKED", exact=True
        ).get_by_role("button", name="Add symbol")
    ).to_be_disabled()
    discovery.get_by_label("Find provider symbol").fill("missing-symbol")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    expect(discovery).to_contain_text("No symbols matched")
    checks.append("Unavailable and empty discovery results provide actionable states")
    discovery.get_by_label("Find provider symbol").fill("")
    discovery.get_by_role("button", name="Search symbols", exact=True).click()
    expect(discovery).to_contain_text("5 matches")
    page.screenshot(
        path=str(root / "artifacts/instrument-discovery-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    mobile = page.locator(".mobile-panel .market-chart-panel")
    expect(mobile).to_be_visible()
    mobile.locator(".instrument-discovery > summary").click()
    mobile.get_by_label("Discovery market").select_option("alpaca")
    mobile.get_by_label("Find provider symbol").fill("NVDA")
    mobile.get_by_role("button", name="Search symbols", exact=True).click()
    expect(
        mobile.get_by_role("article", name="Discovered NVDA", exact=True)
    ).to_contain_text("Saved · Stock")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(
        path=str(root / "artifacts/instrument-discovery-mobile.png"), full_page=True
    )
    checks.append(
        "Desktop and mobile discovery preserve saved selections without horizontal overflow"
    )
    jobs = page.evaluate("async()=>await(await fetch('/api/v1/analysis')).json()")
    assert not jobs
    assert not errors, errors
    browser.close()
(root / "artifacts/instrument-discovery-browser-results.json").write_text(
    json.dumps({"offline_fixture": True, "checks": checks}, indent=2) + "\n"
)
print(f"{len(checks)} discovery browser workflows passed (offline providers)")
