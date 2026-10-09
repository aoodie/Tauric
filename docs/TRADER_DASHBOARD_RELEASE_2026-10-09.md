# Trader dashboard release verification — 9 October 2026

## Verified installation

Application source `82a79763fa4566a138ce3d5b3c803680fa6036ff` was installed on the IONOS VPS at 21:34 BST on 9 October 2026. This report is a later documentation-only commit; the application image revision remains the source above.

- Branch: `codex/trader-dashboard`; [pull request 9](https://github.com/aoodie/Tauric/pull/9).
- Successful immutable [release run](https://github.com/aoodie/Tauric/actions/runs/37984677526); four published amd64/arm64 images, actual amd64 installation verified.
- Active release: `/srv/tauric/releases/trader-dashboard-82a79763fa45/tauric-release`; private state remains `/srv/tauric-private`.
- Release archive SHA-256: `88ae6e4298490724e1d29adea5dfc722407fca82a925de74e2659ab14b9cb9b9`; 29 allowlisted public files checked before and after staging.
- Database: `0035_scanner_budget`, 79 tables. All 15 services healthy; all 14 application containers match the release revision and verified image identities.
- All 145 Python module hashes match committed source. All 23 execution-critical modules retain signature `dab1a0b89d59a17a17476eaf3979c595c28d86495ffacc31bf2c4d8218bacadc`.
- API, chat worker, its loaded instructions and OpenClaw's chat workspace match instruction SHA-256 `0b262852417af8fbb2c3ea88df5b8d613172d09033bcc422cbe5b4c65f07577e`. Gateway startup/configuration also match the release. Web assets are served from the immutable image without the earlier static overlay.

| Image | Immutable reference |
|---|---|
| api | `ghcr.io/aoodie/tauric-api@sha256:b0a7a54cc6778ba084756b19152248a1e363e5637d20ab044146abcf6ff8caac` |
| worker | `ghcr.io/aoodie/tauric-worker@sha256:e5692446a9a6549f38bf8a10194e42b93820c8fe63beec835d59da9ba4caa119` |
| web | `ghcr.io/aoodie/tauric-web@sha256:b2f6b5659c53334ceb10ab12ec3e183c09f2c5d717d9f948beb99fde3e23a055` |
| openclaw | `ghcr.io/aoodie/tauric-openclaw@sha256:16fa8bdff0de3bac57a126386b10ceca76ebb9229e5ed0a39af65f8466fe4e0d` |

## Implementation and checks

The dashboard presents versioned deterministic multifactor setup scores, component evidence, structural geometry, score/market/timeframe filters and governed watch/research/paper controls. Catalog scanning uses bounded resumable slices and persistent shared candle budgets. Pure candle detection/scoring makes zero model calls. A setup score measures rules, not probability or demonstrated profitability.

The dockable OpenClaw feed projects existing authoritative work, verifies immutable receipts, follows asynchronous outcomes, distinguishes actors, pages history and retains visibly stale last-good data during outages. Verified live projection: 37 activities, 32 attributed to OpenClaw, 0 current work records; status counts `{"completed": 35, "failed": 2}`. These are observations at the recorded check time, not a guarantee of unchanged future activity.

| Check | Evidence |
|---|---|
| Full hosted backend suite | 1083 passed; 22 browser tests deselected |
| Deployment/registry Python controls | 14 passed |
| Encrypted registry handoff controls | 3 Node tests passed |
| Native artifact/feed regression | 9 focused tests passed; actual native writer, strict incorrect-format/corruption rejection and missing-artifact blocking |
| Frontend formatting and production build | Passed; existing large-chunk warning remains |
| Pinned OpenClaw SDK/plugin/configuration and exact backend operation contract | Passed |
| Built-UI browser workflows | Seven unique workflows passed before the backend-only checksum correction; frontend source unchanged |
| Hosted clean install, API/whole-stack persistence and complete restore | Passed; no real model or broker calls |

The browser workflows cover the dashboard/feed and mobile layout, independent scanners, open research and four market-adapter workflows. Hosted default checks still exclude browser tests. The preserved read-only open research backend and independently deployed research UI are consolidated into the final images.

## Backup, restoration and preservation

The first installation rehearsed `0033_worker_progress` → `0035_scanner_budget`, preserving all 76 existing data tables. The checksum correction changes no schema: the isolated restore and production migration are no-ops, preserving all 78 existing data tables plus the same schema revision.

The complete correction rehearsal restored `/srv/private-backups/tauric-before-native-checksum-20261009.tar.gz` (SHA-256 `79eaa213970cd312f5b339b6aa1ea8acbcab23d42cd564d55fca6bcf8f5804b3`): all 79 table hashes and four runtime volumes matched. Recovered application/background services were never started, and `RESTORED_REVIEW_REQUIRED` remains in place. The earlier completed restore database was stopped after verification.

A further fresh pre-installation backup was created at `/srv/private-backups/tauric-before-trader-82a79763fa45-20261009T203239Z.tar.gz`, SHA-256 `145d12ccd7a7562524752d1cb49056d8260202ee8e534549aeac6b5688bb8ddd`, with 79 tables and 4 runtime volumes. Previous releases, backups and prior private image settings are retained for governed recovery.

After startup, 33 protected authority/settings tables were unchanged. All 2880 pre-start readiness records retained their exact count/hash; 1 new records were checksum-valid automatic valuation observations. Credentials and all non-image settings were unchanged. No pause, research allowance, qualification, live activation or broker route was changed.

The initial preservation guard compared the whole readiness table and flagged normal post-start observations. Verification was corrected to preserve historical rows and separately verify the kind/checksum of new observations. The first activity check also found that native research uses a different sorted-JSON checksum format from compact assistant receipts. The final source verifies each artifact with its original writer's exact format; historical evidence was never rewritten.

An initial correction pre-installation probe exited without diagnostic output before any production mutation. Service/state checks confirmed no installation change; independent read-only connection and execution-signature probes passed, and the guarded installation retry completed. The original probe's precise cause remains undetermined; immediate healthy checks do not establish a 24-hour capacity result.

## Public ingress and limits

Trusted HTTPS readiness and the published dashboard/feed/research assets passed at [the workstation](https://217.160.9.196). Unauthenticated activity access returned 401. The owner's saved login was already rejected and had not changed; no repeated failed login or password reset was used. Consequently authenticated production HTTP/browser acceptance remains unverified. Authenticated built-UI acceptance used disposable fixtures, and the production feed was independently checked through a read-only service projection.

This release does not complete general autonomous ownership, certify scanner profitability or qualify the failed EUR/USD strategy. Durable goal supervision, isolated general engineering and restart/resume acceptance are separate work. Connected unattended broker lifecycle, representative 24-hour soak, scheduled encrypted off-host backup retention and future certificate renewal remain open operational gates. The frozen failed strategy, halted account and existing financial controls remain preserved.

The temporary registry handoff requested pull-only access for the four image repositories and consumed only verified release digests. It used an ephemeral encrypted recipient and private RAM configuration, and left permanent registry credentials unchanged. Consumed encrypted access artifacts and the local ephemeral key were removed after verified transfer.

## Evidence records

Public local observations are retained under `artifacts/trader-dashboard-*`: corrected hosted checks, immutable bundle/smoke result, source hashes, backup/restore, migration, deployment, postcheck and trusted HTTPS result. Host verification records are private under `/srv/tauric-private/trader-dashboard-*-82a79763fa45.json`. Private recovery archives remain under `/srv/private-backups`.

See [the architecture/security/quant audit](AUDIT_TRADER_DASHBOARD_2026-10-09.md) and [implementation specification](TRADER_DASHBOARD_IMPLEMENTATION.md) for the five-area audit, exact scoring contracts and remaining acceptance gates.
