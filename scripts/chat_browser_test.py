"""Browser interaction on disposable DB and explicit offline chat transport."""

import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads(
    Path(
        os.environ.get("TAURIC_BROWSER_FIXTURE", root / "artifacts/browser-test.json")
    ).read_text()
)
results = []
message_text = "Explain the three chat channels " + uuid4().hex[:6]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1536, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(os.environ.get("TAURIC_BROWSER_URL", "http://127.0.0.1:5173"))
    page.get_by_label("Username", exact=True).fill(fixture["username"])
    page.get_by_label("Password", exact=True).fill(fixture["password"])
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    expect(page.get_by_role("heading", name="Chat with OpenClaw")).to_be_visible()
    expect(page.get_by_text("Ready", exact=True)).to_be_visible()
    page.get_by_label("Message OpenClaw", exact=True).fill(message_text)
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.get_by_role("button", name="Send", exact=True)).to_be_disabled()
    expect(
        page.get_by_text("Offline browser fixture reply:", exact=False)
    ).to_be_visible(timeout=12000)
    results.append("Web send, queued/running reply, composer guard and saved history")
    page.get_by_role("button", name="New conversation", exact=True).click()
    expect(
        page.get_by_role("heading", name="What would you like to explore?")
    ).to_be_visible()
    page.get_by_role("button", name=message_text, exact=True).click()
    expect(
        page.get_by_text("Offline browser fixture reply:", exact=False)
    ).to_be_visible()
    page.reload()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_role("button", name=message_text, exact=True).click()
    expect(
        page.get_by_text("Offline browser fixture reply:", exact=False)
    ).to_be_visible()
    results.append("New conversations isolate history and saved replies survive reload")
    page.get_by_role("button", name="New conversation", exact=True).click()
    page.get_by_label("Message OpenClaw", exact=True).fill("Run daily BTC research")
    page.get_by_role("button", name="Send", exact=True).click()
    card = page.get_by_label("binance:spot:BTCUSDT research run", exact=True)
    expect(card).to_contain_text("queued", timeout=12000)
    expect(card).to_contain_text("succeeded", timeout=12000)
    page.get_by_role("button", name="Open original report", exact=True).click()
    report = page.get_by_role("dialog", name="Tauric research report")
    report.get_by_role("button", name="Market analysis", exact=True).click()
    expect(report.locator(".report-content")).to_contain_text(
        "Untouched offline native market report."
    )
    page.get_by_role("button", name="Close details", exact=True).click()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_role("button", name="Run daily BTC research", exact=True).click()
    expect(card).to_contain_text("succeeded")
    page.get_by_role("button", name="New conversation", exact=True).click()
    page.get_by_label("Message OpenClaw", exact=True).fill(
        "Run daily ETH research failure"
    )
    page.get_by_role("button", name="Send", exact=True).click()
    expect(
        page.get_by_label("binance:spot:ETHUSDT research run", exact=True)
    ).to_contain_text("Offline fixture research failure", timeout=12000)
    expect(
        page.get_by_role("button", name="Open original report", exact=True)
    ).to_have_count(0)
    results.append(
        "Research queue cards, original report links, persistent history and visible failures"
    )
    page.get_by_role("button", name="Chat connections", exact=True).click()
    expect(
        page.get_by_role("tab", name="Chat connections", exact=True)
    ).to_have_attribute("aria-selected", "true")
    for channel, owner in [("telegram", "12345678"), ("discord", "123456789012345678")]:
        page.get_by_label(
            "Enable " + channel.title() + " owner DMs", exact=True
        ).check()
        page.get_by_label(channel + " owner user ID", exact=True).fill(owner)
        page.get_by_label(channel + " token action", exact=True).select_option(
            "replace"
        )
        page.get_by_label(channel + " new bot token", exact=True).fill(
            "offline-browser-" + channel + "-secret"
        )
    page.get_by_label("Chat daily research allowance", exact=True).fill("0")
    page.get_by_role("button", name="Save chat connections", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Saved.")
    expect(page.get_by_label("telegram token action", exact=True)).to_have_value("keep")
    expect(page.get_by_label("telegram new bot token", exact=True)).to_have_count(0)
    data = page.evaluate("async()=>await(await fetch('/api/v1/chat/settings')).json()")
    assert data["telegram"]["has_token"] and data["discord"]["has_token"]
    assert data["daily_research_allowance"] == 0 and data["research_used_today"] == 2
    assert "offline-browser" not in json.dumps(data)
    results.append("Both bot Settings save encrypted credentials and masked reloads")
    page.get_by_label("telegram owner user ID", exact=True).fill("*")
    page.get_by_role("button", name="Save chat connections", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("numeric user ID")
    results.append("Public sender wildcard rejected visibly")
    page.get_by_role("tab", name="Security", exact=True).click()
    expect(page.get_by_label("Current password", exact=True)).to_be_visible()
    results.append("Password changes remain accessible in Settings")
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_label("Message OpenClaw", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(root / "artifacts/chat-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1536, "height": 1000})
    page.get_by_role("button", name=message_text, exact=True).click()
    expect(
        page.get_by_text("Offline browser fixture reply:", exact=False)
    ).to_be_visible()
    page.screenshot(path=str(root / "artifacts/chat-desktop.png"), full_page=True)
    results.append("Desktop/mobile layout without page overflow")
    assert not errors, errors
    browser.close()
(root / "artifacts/chat-browser-results.json").write_text(
    json.dumps(results, indent=2) + "\n"
)
print(json.dumps({"passed": len(results), "checks": results}, indent=2))
