"""Exercise native-independent research on a disposable local server."""

import json
import re
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
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill("test-owner")
    page.get_by_label("Password", exact=True).fill("Test-only-password-2026")
    page.get_by_role("button", name="Sign in", exact=True).click()
    chart = page.locator(".desktop-layout .market-chart-panel")
    expect(chart).to_be_visible()
    chart.get_by_label("Every 30s", exact=True).uncheck()
    chart.get_by_label("Chart instrument", exact=True).select_option(
        "test:unclassified:EUR_USD"
    )
    expect(chart.get_by_role("button", name="Research", exact=True)).to_be_enabled()
    chart.get_by_role("button", name="Research", exact=True).click()
    panel = page.locator(".desktop-layout .native-panel")
    form = panel.get_by_role("form", name="Open market research")
    expect(form.get_by_label("Research pair or market")).to_have_value("EUR/USD")
    expect(form.get_by_role("button", name="Ask OpenClaw", exact=True)).to_be_enabled()
    expect(
        panel.get_by_role("button", name="Run daily analysis", exact=True)
    ).to_have_count(0)
    checks.append(
        "An instrument without native eligibility can open active research with no native worker"
    )

    form.get_by_label("Research pair or market").fill("XAG/USD")
    form.get_by_label("Research timeframe").fill("0m")
    form.get_by_role("button", name="Ask OpenClaw", exact=True).click()
    expect(form).to_be_visible()
    form.get_by_label("Research timeframe").fill("1w")
    form.get_by_role("button", name="Ask OpenClaw", exact=True).click()
    draft = page.get_by_label("Message OpenClaw", exact=True)
    expect(draft).to_have_value(re.compile("XAG/USD.*1w.*open research"))
    checks.append(
        "An unregistered metal and weekly timeframe reach the chat draft unchanged"
    )

    page.get_by_role("button", name="Workspace", exact=True).click()
    form = page.locator(".desktop-layout .native-panel").get_by_role(
        "form", name="Open market research"
    )
    form.get_by_label("Research pair or market").fill("SI=F")
    form.get_by_label("Research timeframe").fill("7m")
    form.get_by_role("button", name="Ask OpenClaw", exact=True).click()
    expect(draft).to_have_value(
        re.compile("SI=F.*7m.*keep spot, CFDs, futures and ETFs distinct")
    )
    checks.append(
        "Futures and custom timeframes retain their requested product identity"
    )

    page.get_by_role("button", name="Workspace", exact=True).click()
    chart.get_by_label("Chart instrument").select_option("binance:spot:BTCUSDT")
    panel = page.locator(".desktop-layout .native-panel")
    expect(
        panel.get_by_role("button", name="Run daily analysis", exact=True)
    ).to_be_disabled()
    expect(panel.get_by_role("button", name="Ask OpenClaw", exact=True)).to_be_enabled()
    expect(panel.locator(".native-scope strong")).to_have_text("BTC/USDT")
    checks.append(
        "Existing built-in reports retain their connection checks without blocking open research"
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("Active panel", exact=True).select_option("tauric")
    form = page.locator(".mobile-panel").get_by_role(
        "form", name="Open market research"
    )
    form.get_by_label("Research pair or market").fill("XAU/USD")
    form.get_by_label("Research timeframe").fill("1mo")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(
        path=str(root / "artifacts/open-research-mobile.png"), full_page=True
    )
    form.get_by_role("button", name="Ask OpenClaw", exact=True).click()
    expect(draft).to_have_value(re.compile("XAU/USD.*1mo"))
    checks.append(
        "Mobile research accepts gold and monthly context without horizontal overflow"
    )
    jobs = page.evaluate("async () => await (await fetch('/api/v1/analysis')).json()")
    assert not jobs
    assert not errors, errors
    browser.close()
(root / "artifacts/open-research-browser-results.json").write_text(
    json.dumps(
        {"disposable_account": True, "checks": checks, "native_jobs_created": 0},
        indent=2,
    )
    + "\n"
)
print(f"{len(checks)} native-independent research browser checks passed")
