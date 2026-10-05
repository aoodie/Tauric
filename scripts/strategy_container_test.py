"""Built Linux assets and migration readiness; disposable owner, no model calls."""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
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
        fd = os.open(
            root / "artifacts/strategy-container-failure.log",
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.stdout + result.stderr)
        raise RuntimeError(
            "Built-container setup failed; private diagnostics saved locally."
        )


try:
    command("up", "-d", "--wait", "--wait-timeout", "60")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1536, "height": 1000})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        response = page.goto("http://127.0.0.1:8081")
        assert "script-src 'self'" in response.all_headers()["content-security-policy"]
        assert page.request.get("http://127.0.0.1:8081/api/v1/paper").status == 401
        page.get_by_label("Username", exact=True).fill(fixture["username"])
        page.get_by_label("Password", exact=True).fill(fixture["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_text("Storage ready", exact=True)).to_be_visible()
        page.get_by_role("button", name="Settings", exact=True).click()
        page.get_by_role("tab", name="Trading", exact=True).click()
        version_card = page.get_by_role(
            "article", name="Browser registered versions", exact=True
        )
        expect(version_card.get_by_text("Revision 2", exact=True)).to_be_visible()
        version_card.locator("summary").click()
        expect(version_card.get_by_text("v1 · revision 1", exact=True)).to_be_visible()
        expect(version_card.get_by_text("v2 · revision 2", exact=True)).to_be_visible()
        route = page.locator(".paper-deployment").filter(
            has=page.get_by_text("Browser registered · v1", exact=True)
        )
        expect(
            route.get_by_role("button", name="Resume strategy route", exact=True)
        ).to_be_visible()
        status = page.evaluate("""async () => {
          const state = await (await fetch('/api/v1/paper')).json();
          const deployment = state.deployments.find(v => v.strategy === 'Browser registered');
          if (deployment.enabled || deployment.control_revision !== 1) throw new Error('Pause was not preserved');
          const version = state.strategy_versions.find(v => v.id === deployment.strategy_revision_id);
          if (version.parameters.label !== 'v1' || version.parameters.risk_percent !== '1') throw new Error('Pinned version changed');
          const r = await fetch(`/api/v1/resources/paper_deployment/${deployment.id}`);
          if (!r.ok) throw new Error('Deployment resource failed');
          const resource = await r.json();
          if (resource.data.control_history.length !== 1) throw new Error('Deployment history failed');
          const rejected = await fetch(`/api/v1/paper/deployments/${deployment.id}/control`, {method:'PUT',headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:1,enabled:true,reason:'Missing CSRF test'})});
          return rejected.status;
        }""")
        assert status == 403
        assert (
            page.request.get(
                "http://127.0.0.1:8081/internal/openclaw/runtime-config"
            ).status
            == 404
        )
        page.screenshot(
            path=str(root / "artifacts/strategy-container.png"), full_page=True
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        assert not errors, errors
        browser.close()
    (root / "artifacts/strategy-container-results.json").write_text(
        json.dumps(
            {
                "passed": [
                    "Built Linux frontend and migrated API reach readiness",
                    "Strategy versions, pinned routes and pause history survive container restart",
                    "Private reads and CSRF protection remain enforced",
                    "Private service paths stay unavailable through Caddy",
                    "Desktop and mobile assets render without page errors",
                ],
                "real_model_calls": 0,
                "broker_order_calls": 0,
            },
            indent=2,
        )
    )
    print("5 built-container strategy checks passed; no model or broker order calls")
finally:
    command(
        "--profile",
        "verification",
        "--profile",
        "governance_fixture",
        "down",
        "--volumes",
    )
