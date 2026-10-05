"""Built UI checks; the pytest harness supplies an isolated database and offline chat."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
url, first, second = sys.argv[1:]
if not url.startswith("http://127.0.0.1:"):
    raise SystemExit("Use the disposable local test server.")
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
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    page.get_by_label("Conversation context", exact=True).select_option(first)
    workspace = page.get_by_role("region", name="Portfolio workspace")
    # Sections without headings are named by aria-label but expose a generic role in some browsers.
    workspace = page.locator(".portfolio-panel")
    expect(workspace).to_contain_text("Scalping")
    expect(workspace).to_contain_text("1 attributed deployments")
    page.get_by_text("Portfolio settings and chat permissions", exact=True).click()
    mandate = page.get_by_label("Portfolio mandate", exact=True)
    mandate.fill("Scalp crypto with higher net returns inside my limits")
    page.get_by_role("button", name="Save portfolio settings", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Portfolio settings saved")
    expect(
        page.get_by_role("button", name="Save portfolio settings", exact=True)
    ).to_be_enabled()
    with page.expect_response(
        lambda r: "/api/v1/portfolios/" in r.url and r.url.endswith("/history")
    ) as history_response:
        page.get_by_role(
            "button", name="View portfolio settings history", exact=True
        ).click()
    assert history_response.value.status == 200
    expect(page.locator(".portfolio-history")).to_contain_text("Revision 2")
    checks.append("Versioned mandate saves and history")
    # The Settings form uses the same versioned service; force a stale concurrent save.
    page.evaluate(
        """async id=>{
      const session=await(await fetch('/api/v1/session')).json();
      const c=await(await fetch('/api/v1/portfolios/'+id)).json();
      const r=await fetch('/api/v1/portfolios/'+id,{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify({request_id:crypto.randomUUID(),expected_revision:c.portfolio.revision,allocation_ids:c.portfolio.allocation_ids,preferences:{...c.portfolio.preferences,research_objective:'balanced'},reason:'Concurrent disposable browser update'})});
      if(!r.ok) throw Error('Fixture concurrent save failed');
    }""",
        first,
    )
    mandate.fill("Stale browser change must be rejected")
    page.get_by_role("button", name="Save portfolio settings", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Portfolio settings changed")
    page.get_by_role("button", name="Reload saved settings", exact=True).click()
    expect(mandate).to_have_value(
        "Scalp crypto with higher net returns inside my limits"
    )
    checks.append("Stale settings rejection and explicit reload")
    page.get_by_label("Message OpenClaw", exact=True).fill(
        "Pause my existing paper strategy"
    )
    page.get_by_role("button", name="Send", exact=True).click()
    expect(
        page.get_by_text("Offline portfolio fixture: pause saved.", exact=False)
    ).to_be_visible(timeout=15000)
    expect(page.locator(".chat-research")).to_contain_text(
        "Saved portfolio action: pause"
    )
    page.get_by_label("Conversation context", exact=True).select_option(second)
    expect(page.locator(".portfolio-panel")).to_contain_text("Mixed")
    expect(
        page.get_by_text("Offline portfolio fixture: pause saved.", exact=False)
    ).to_have_count(0)
    expect(
        page.get_by_role("button", name="Pause my existing paper strategy", exact=True)
    ).to_have_count(0)
    page.get_by_label("Conversation context", exact=True).select_option(first)
    page.get_by_role(
        "button", name="Pause my existing paper strategy", exact=True
    ).click()
    expect(
        page.get_by_text("Offline portfolio fixture: pause saved.", exact=False)
    ).to_be_visible()
    checks.append("Scoped conversations and durable action receipt")
    page.reload()
    page.get_by_role("button", name="OpenClaw chat", exact=True).click()
    expect(page.get_by_label("Conversation context", exact=True)).to_have_value(first)
    page.get_by_role(
        "button", name="Pause my existing paper strategy", exact=True
    ).click()
    expect(
        page.get_by_text("Offline portfolio fixture: pause saved.", exact=False)
    ).to_be_visible()
    page.screenshot(
        path=str(root / "artifacts/portfolio-chat-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_label("Message OpenClaw", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    page.screenshot(
        path=str(root / "artifacts/portfolio-chat-mobile.png"), full_page=True
    )
    checks.append("Reload persistence and mobile layout")
    assert not errors, errors
    browser.close()
(root / "artifacts/portfolio-browser-results.json").write_text(
    json.dumps({"offline_fixture": True, "checks": checks}, indent=2) + "\n"
)
print(f"{len(checks)} portfolio browser checks passed (offline gateway)")
