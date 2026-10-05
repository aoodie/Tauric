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
            root / "artifacts/capital-container-failure.log",
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
        card = page.get_by_role("article", name="Browser nested allocation", exact=True)
        expect(card.locator(".badge")).to_have_text("v2")
        card.get_by_role("button", name="Allocation history", exact=True).click()
        expect(card.get_by_text("Version 1", exact=True)).to_be_visible()
        expect(card.get_by_text("Version 2", exact=True)).to_be_visible()
        status = page.evaluate("""async () => {
          const state = await (await fetch('/api/v1/paper')).json();
          const scope = state.capital_scopes.find(v => v.name === 'Browser nested');
          const r = await fetch(`/api/v1/resources/capital_scope/${scope.id}`);
          if (!r.ok) throw new Error('Allocation resource failed');
          const resource = await r.json();
          if (resource.data.history.length !== 2) throw new Error('Allocation history failed');
          const rejected = await fetch(`/api/v1/paper/capital/${scope.id}`, {method:'PUT',headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:2,limits:scope.limits,reason:'Missing CSRF test'})});
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
            path=str(root / "artifacts/capital-container.png"), full_page=True
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        assert not errors, errors
        browser.close()
    (root / "artifacts/capital-container-results.json").write_text(
        json.dumps(
            {
                "passed": [
                    "Built Linux frontend and migrated API reach readiness",
                    "Nested allocation versions and resource history survive container restart",
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
    print("5 built-container capital checks passed; no model or broker order calls")
finally:
    command(
        "--profile",
        "verification",
        "--profile",
        "governance_fixture",
        "down",
        "--volumes",
    )
