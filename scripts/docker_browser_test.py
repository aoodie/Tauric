"""Verify built containers and Caddy headers using the disposable browser owner."""

import json
import os
import subprocess
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
# The DSN password is kept in process environment and is never echoed.
from urllib.parse import urlsplit, urlunsplit

url = urlsplit(fixture["database_url"])
credentials = url.netloc.rsplit("@", 1)[0]
environment = os.environ.copy()
environment["BROWSER_DATABASE_URL"] = urlunsplit(
    (url.scheme, credentials + "@db:5432", url.path, url.query, url.fragment)
)
compose = ["docker", "compose", "-f", str(root / "deploy/compose.browser.yml")]


def command(*args):
    result = subprocess.run(
        [*compose, *args], env=environment, capture_output=True, check=False
    )
    if result.returncode:
        import os

        fd = os.open(
            root / "artifacts/docker-browser-failure.log",
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.stdout + result.stderr)
        raise RuntimeError(
            "Container browser setup failed; inspect private Docker logs locally."
        )


try:
    command("up", "-d", "--wait", "--wait-timeout", "60")
    command("run", "--rm", "--no-deps", "model_reader")
    command("up", "-d", "--wait", "--wait-timeout", "120", "test_openclaw")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda event: errors.append(event.text) if event.type == "error" else None,
        )
        response = page.goto("http://127.0.0.1:8081")
        page.wait_for_load_state("networkidle")
        headers = response.all_headers()
        assert "script-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        page.get_by_label("Username", exact=True).fill(fixture["username"])
        page.get_by_label("Password", exact=True).fill(fixture["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_text("Storage ready", exact=True)).to_be_visible()
        expect(page.get_by_text("Saved · v5", exact=True)).to_be_visible()
        page.get_by_label("Workspace", exact=True).select_option(label="Research copy")
        expect(page.get_by_text("Saved · v3", exact=True)).to_be_visible()
        page.get_by_role("button", name="Settings", exact=True).click()
        row = page.locator(".setting-row").filter(
            has=page.get_by_role("heading", name="Interface density", exact=True)
        )
        expect(row.get_by_text("From", exact=False)).to_contain_text("user · v4")
        page.get_by_role("tab", name="AI connections", exact=True).click()
        expect(
            page.get_by_role("form", name="Direct provider API settings", exact=True)
        ).to_be_visible()
        page.get_by_role("tab", name="Security", exact=True).click()
        expect(
            page.get_by_role("heading", name="Change password", exact=True)
        ).to_be_visible()
        page.get_by_role("tab", name="Trading", exact=True).click()
        expect(
            page.get_by_role("heading", name="Browser paper", exact=True)
        ).to_be_visible()
        expect(
            page.get_by_role("button", name="Resume paper entries", exact=True)
        ).to_be_visible()
        page.get_by_role("button", name="Paper trading", exact=True).click()
        expect(page.get_by_text("No open paper positions.", exact=True)).to_be_visible()
        expect(
            page.get_by_text("PAPER DECISIONS & TRADES", exact=False)
        ).to_be_visible()
        assert (
            page.request.get(
                "http://127.0.0.1:8081/internal/openclaw/proposals/unused"
            ).status
            == 404
        )
        proposal_id = page.evaluate(
            "async () => {const s=await (await fetch('/api/v1/paper')).json(); return s.records.find(r=>r.kind==='proposal').id;}"
        )
        # Read-only actual Gateway -> plugin -> isolated API; no model invocation.
        command(
            "exec",
            "-T",
            "-e",
            "TEST_PROPOSAL=" + proposal_id,
            "test_openclaw",
            "node",
            "-e",
            """
    const fs=require('node:fs');
    const config=JSON.parse(fs.readFileSync('/state/service-tokens.json','utf8'));
    const configured=Object.keys(JSON.parse(fs.readFileSync('/state/openclaw.json','utf8')).models?.providers??{}).length>0;
    (async()=>{
     const context=await fetch('http://api:8000/api/v1/paper'); if(context.status!==401)throw Error('Owner boundary failed');
     const response=await fetch('http://127.0.0.1:18789/healthz'); if(!response.ok)throw Error('Gateway unhealthy');
     const chat=await fetch('http://127.0.0.1:18789/v1/chat/completions',{method:'POST',headers:{Authorization:'Bearer '+config.gateway_token,'Content-Type':'application/json'},body:JSON.stringify({model:"openclaw:governor-cloud",messages:[]})});if(![400,404].includes(chat.status))throw Error('Invalid private request must not start a model run');
     const denied=await fetch('http://127.0.0.1:18789/tools/invoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:'tauric_paper_context',args:{proposal_id:process.env.TEST_PROPOSAL}})});if(denied.status!==401)throw Error('Gateway token required');
     const responseTool=await fetch('http://127.0.0.1:18789/tools/invoke',{method:'POST',headers:{Authorization:'Bearer '+config.gateway_token,'Content-Type':'application/json'},body:JSON.stringify({tool:'tauric_paper_context',args:{proposal_id:process.env.TEST_PROPOSAL}})});
     let contextResult;
     if(configured) {
       if(responseTool.status!==404)throw Error('Governors must exclude the unbound proposal tool');
       const privateRead=await fetch('http://api:8000/internal/openclaw/proposals/'+process.env.TEST_PROPOSAL,{headers:{Authorization:'Bearer '+config.platform_token}});
       if(!privateRead.ok)throw Error('Private paper context unavailable');
       contextResult=await privateRead.json();
     } else {
       if(!responseTool.ok)throw Error('Tool unavailable');
       const result=await responseTool.json(); if(!result.ok)throw Error('Tool context failed');contextResult=result.result?.details;
     }
     if(contextResult?.authority!=='read_only_paper_context' || contextResult.proposal.id!==process.env.TEST_PROPOSAL)throw Error('Private context mismatch');
     console.log('Actual private Gateway healthy; owner boundary and invalid model request blocked.');
    })().catch(error=>{console.error(error.message);process.exit(1)});
    """,
        )
        page.get_by_role("button", name="Sign out", exact=True).click()
        expect(
            page.get_by_role("heading", name="Open your workstation")
        ).to_be_visible()
        # The initial anonymous session probe intentionally returns 401.
        unexpected = [error for error in errors if "401" not in error]
        assert not unexpected, unexpected
        browser.close()
    (root / "artifacts/docker-browser-results.json").write_text(
        json.dumps(
            {
                "passed": [
                    "Built assets and Caddy security headers",
                    "Container login and persisted settings/workspaces",
                    "Container logout",
                    "Built paper Settings, persisted halt, balances and journal",
                    "Actual separate OpenClaw runtime, private context, governor tool restrictions and invalid model requests blocked",
                    "Built worker reads encrypted Settings connections and built Settings tabs render",
                ],
                "unexpected_browser_errors": [],
            },
            indent=2,
        )
    )
    print(
        "Built-container checks passed: 6 workflows, including paper persistence and separate OpenClaw runtime."
    )
finally:
    command("--profile", "verification", "down", "--volumes")
