# Workstation foundation verification

Milestone: local development foundation, version 0.1.0. Verified on 2 October 2026 using macOS arm64, Docker Desktop Linux arm64 containers, PostgreSQL 17 and Chromium. This is not the full trading release or a verified IONOS installation.

## Implemented

- One owner created through a local administrator command; Argon2 password hashing, opaque server-side sessions, logout revocation, expiry and persistent login throttling. No public owner registration.
- Cookie/Origin/CSRF checks for browser writes, redacted validation errors and separate migration/runtime database roles. HTTPS cookie configuration is tested; the local HTTP development profile explicitly disables Secure cookies.
- Four persisted schema-driven preferences with default/global/user resolution, source labels, change reasons, expected revisions and restore-as-new-version.
- Dockable workspaces with selected tabs, divider sizing, maximize/restore, content collapse/expand, close/restore, explicit save, clone, reset and layout history. Narrow screens use a panel selector. Concurrent changes cannot silently replace a saved revision.
- Append-only settings/layout/audit history and setting definitions, protected by PostgreSQL permissions and mutation/truncate triggers. An administrator can deliberately remove those protections; this is not a tamper-proof external ledger.
- Shared resource references, detail drawer/full view, hash links and paginated activity. Honest process/storage status and unconnected trading states.
- Locked Python/npm dependencies, generated OpenAPI TypeScript types, frozen initial migration, pinned Docker base image digests and a Caddy frontend/API proxy.

## Recorded checks

| Check | Result | Evidence |
|---|---|---|
| PostgreSQL/API checks | 23 passed | `backend/tests/test_foundation.py`, disposable databases; final run 5.33 seconds |
| Owner creation | Passed | Browser fixture uses the actual CLI with temporary credentials; overwrite refusal is also tested |
| Session and request protection | Passed | Unauthorized reads, origin/CSRF failures, expiry, logout, cookie attributes, HTTPS Secure flag, throttle and secret redaction |
| Configuration and layout consistency | Passed | Validation, inheritance, history restoration and parallel writes with exactly one accepted update |
| Immutable history | Passed | Runtime cannot disable triggers; ordinary admin update/delete/truncate fail for all four protected tables |
| Browser owner workflows | 11 passed | `scripts/browser_test.py`, `artifacts/browser-results.json`; no page errors |
| Actual docking and resizing | Passed | Browser drags Scanner into analysis dock, verifies common saved parent, changes divider and verifies persisted layout differs |
| Desktop and narrow screens | Passed | Saved selection, collapse/expand, close/restore, maximize/restore, settings conflict/reload, resource drawer, keyboard focus and 390px view without document overflow |
| Real database outage and restart | Passed | Browser sees unavailable storage, attempted change fails; DB restart restores readiness and existing session/settings/layouts remain readable |
| Docker API restart | Passed | Service restarts during browser test; separate host API reads preserved PostgreSQL state. Full built-container checks are recorded separately |
| Production frontend build | Passed | TypeScript and Vite build; 466.59 KB JS / 132.86 KB gzip, 57.38 KB CSS / 10.11 KB gzip |
| Code checks | Passed | Ruff and Prettier |
| Pinned-image container builds | Passed | API, migration and Caddy/frontend images build for Linux arm64 |
| Built-container browser checks | 3 passed | `scripts/docker_browser_test.py`, isolated API/web containers: Caddy security headers, login, persisted preferences/layouts and logout; no unexpected browser errors |

Backend tests emit one upstream Starlette warning about a future `httpx2` test-client dependency. It does not fail the checks; revisit the dependency lock when upgrading the test runtime.

Screenshots are local, ignored artifacts: `artifacts/login.png`, `artifacts/workstation.png`, `artifacts/settings.png`, `artifacts/mobile.png`. They show a temporary browser-test owner with no trading data. Temporary credentials and the disposable browser database are removed after container checks.

## Remaining gates

Market data, native Tauric, separate OpenClaw, all model/broker adapters, paper/live execution, financial sizing, reconciliation, trade journals, research, intelligence and notifications remain pending. There are no broker credentials or orders in this build.

Clean Ubuntu/Debian deployment, Linux amd64 validation, HTTPS/public ingress, owner recovery, backup restoration, upgrade/rollback, host reboot, representative soak testing and resource measurements on the actual M+ host remain unverified. See [Server readiness](SERVER_READINESS.md) and [Requirements coverage](REQUIREMENTS.md).
