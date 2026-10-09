# Tauric architecture, security, quant and deployment audit — 9 October 2026

## Decision and scope

The scanner/dashboard/activity milestone can proceed through the tested release process. It does not qualify a strategy or complete autonomous project ownership. The existing EUR/USD strategy failed its frozen research criteria, and connected unattended broker acceptance remains open.

The audit distinguishes source inspection, disposable automated fixtures, live host observations and deployment verification. A passing fixture, job submission or image label alone is insufficient evidence of a live outcome. Read-only host evidence began at 06:29 UTC; an independently deployed open research supplement appeared at 07:03 UTC and was detected during the pre-release audit. A second, independently deployed research UI supplement appeared at 18:57 UTC (source identity `sha256:047639cb1583718fc3da28fa3e440a6b2a81d774434e0e0fae1d5503463d273e`); its five product files and browser acceptance are also consolidated into this release. The new release must preserve that supplement rather than overwrite it.

The working directory belongs to an unrelated parent Git checkout. Release work uses an isolated Tauric checkout and `codex/trader-dashboard`. The source base is `7ee38c9bc5974a85f53c2f20dd2a4ac683813ca7`, plus the verified live open research supplement and this milestone. Separate pending broker-control and Research Lab changes are excluded. No financial setting or explicit pause is altered.

## Findings requiring follow-up

| Priority | Finding | Evidence and required action |
|---|---|---|
| P1 | General autonomous ownership is incomplete | Gateway recurring scheduling is disabled; no shared durable goal registry, general isolated engineering executor, dependency supervisor or restart/resume acceptance exists. Implement the ownership mandate and its behavioral acceptance; do not describe the feed as an orchestration runtime. |
| P1 | The frozen strategy failed research screening | Holdout trade count and both cost-adjusted returns failed. Preserve the failed records and halted account; use a new registered hypothesis and unused evidence for a later trial. |
| P1 | Connected unattended broker lifecycle is unverified | Reprotection and valuation fixtures pass, but the host has no broker policy, order intent or close request. A qualifying native → governed decision → protected fill → attributed exit → reconciliation cycle remains an independent gate. |
| P2 | Deployed code was partly supplied through source overlays | Image revision `18b3d34` did not identify later OpenClaw instructions/tools or open research code. Compare loaded file checksums and preserve deployed additions; consolidate this milestone into matching immutable images. |
| P2 | Release archive omitted files referenced by the recovery override | `compose.gateway-recovery.yml` mounts README, ownership instructions and chat instructions. Add these public source files to the release allowlist and run the real install/restore smoke check. |
| P2 | A deployed plugin advertised pending management operations | The research supplement's plugin included Lab/broker operations absent from the deployed assistant contract. Preserve its working read-only research tools, align management schemas with the released backend, and validate their exact operation sets. A published tool label does not create backend authority. |
| P2 | Authenticated production browser proof previously lacked a matching login file | The 8 October result records a pre-existing password mismatch. Verify an existing authorized signed-in session or current credentials; do not reset the owner password to manufacture acceptance. |
| P2 | Scale and latency remain operational measurements | Catalog progress and shared budgets bound work, but full connected catalog coverage and signal lifetime/latency have not been measured. Record actual coverage, unavailable symbols, retries and representative workload results. |
| P2 | Browser checks remain outside hosted default CI | Hosted backend checks deselect browser tests. This milestone runs real built-UI tests locally; add a maintained critical browser job separately. |
| P2 | Recovery and ingress have remaining long-term gates | Scheduled encrypted off-host backup retention, representative 24-hour soak and observed future certificate renewal remain independent operational acceptance items. A successful immediate restore is not universal rollback certification. |
| P3 | The main frontend bundle remains large | Build succeeds with a Vite warning above 500 kB. Existing chart splitting helps; additional route splitting is a separate performance improvement. |

## 1. Prior P1 gap verification

### FX stop replay

`replay_engine.py:117–123,197–205` calls `stop_trigger_price` for open-gap and intrabar triggers. The executable-side implementation is in `asset_costs.py`; the stop path preserves bid/ask meaning and does not add a second synthetic spread when historical executable sides exist. Gap stops precede a queued crossover exit. Intrabar stop timestamps explicitly assume the bar close because OHLC does not reveal the actual event time.

`test_fx_stop_replay.py:20–59` exercises a bid-only stop touch while midpoint remains higher, a bid-open gap with both zero and nonzero slippage, and a non-touch that must not fabricate a stop fill. Source and fixture evidence support resolution of the reproduced defect. They do not establish intrabar broker parity, historical financing accuracy or profitable strategy behavior.

### Alpaca remainder protection

`broker_positions.py:471–535` requires verified replacement protection for the surviving attributed remainder before completing the close. Replacement submissions and broker facts are reconciled through stable identities. `broker_positions.py:748–826` prevents a fresh repair while a prior protection attempt is active or uncertain, and verifies the remainder before a new attempt.

`test_broker_reprotection.py` covers partial closes, lost replacement responses, rejection, inactive/held/uncertain stop legs, attributed replacement fills, full re-protection after a failed close, and bounded repair without duplicates. The software path is implemented and fixture-tested. The live host's empty broker journals cannot prove a connected broker cycle.

### Marked valuation and currency exposure

`valuation.py:107–194` uses current verified marks for paper NAV, open P&L, entry fees and accrued financing; missing marks keep the observation incomplete. FX positions produce both base and quote currency legs. `valuation.py:285–326` uses executable bid/ask marks for broker holdings, applies quote-to-home conversion, checks attributed quantities against observed holdings and preserves incomplete allocation values.

`test_live_readiness.py:85–148` verifies open losses/entry fees, missing-mark handling and immutable valuation history. Other readiness cases reject insufficient prospective evidence and unauthorized live activation. Source and tests establish implemented valuation, while connected mark continuity and actual broker cost coverage remain operational gates.

## 2. Schema and source/deployment synchronization

The initial live check found `0033_worker_progress`, 77 public tables and 15 healthy services. All 136 loaded Python module hashes at that observation matched source base `7ee38c9`; the images themselves carried `18b3d34473b666cfa83df6679357481bd4c504bb`. This explicitly separates image identity from mounted instructions/source.

Pre-existing local Research Lab work introduced `0034_quant_lab`. The dashboard migration is sequential `0035_scanner_budget`: retain the 0034 compatibility table without installing the separate Lab UI/API/engines, add a persistent market-data token bucket, widen assistant operation storage from 30 to 64 characters, and index owner-scoped activity history and catalog progress. A real API test caught the operation-width mismatch before release and now verifies the complete brief path.

The later live supplement is recorded in `artifacts/open-commodity-deployment.json`, source identity `sha256:eb8fe5f3872e1b6dbc5fe17bba2748d7b7f37256dc92fd876d4c421ac127f06f`. Current host-mounted checksums match the corresponding local open research files. Preserve these files and read-only tools in the immutable release; do not infer that unrelated local changes were also deployed.

Deployment requires a fresh 0033 backup, full isolated restore, rehearsal of 0033 → 0034 → 0035, matching source/image digests and post-installation checks. The final deployed revision and results belong in the release verification record; this pre-release audit is not a deployment claim.

### Container hardening

`deploy/compose.server.yml` sets read-only root filesystems, capability drop `ALL` and UID `10001:10001` for Python application/worker services. The pinned OpenClaw runtime uses `1000:1000`. Internal database/API/Gateway/worker ports are not published. The web ingress deliberately publishes the workstation listener, with the HTTPS/public-IP override exposing 80/443.

Live inspection confirms these controls for application services. The web ingress and PostgreSQL image do not share the Python UID/capability profile; reporting all containers as UID 10001 would be false. Persistent writable volumes and mounted private files remain necessary. Source reads and tools must not expose production financial secrets to a general engineering executor.

## 3. Financial controls and web security

### Execution identity and exact financial math

`live_trading.py:30–64` hashes the ordered concatenation of each filename and its raw bytes for 23 execution-critical modules using SHA-256. It binds broker adapters, sizing, protection/reconciliation, native assessment, data quality, valuation, forward validation and operations progress. This milestone does not edit those modules. Compare their hashes with the verified host and source base before installation; a different signature would invalidate exact-version prospective evidence.

`broker_risk.py:29–78` performs sizing inside Decimal precision 120. It uses account equity × authorized risk percentage / stop-distance loss per unit, quote-to-home loss conversion, broker buying power/margin, minimum/maximum quantities and downward quantity-step rounding. Invalid or absent facts reject sizing. Gaps, slippage, fees and financing can make realized losses larger than the estimate. `financial_contracts.py:9–29` accepts bounded exact decimal strings; it is input validation, not a separate 120-precision calculation engine.

The deployed baseline does not contain the additional local account-policy risk/drawdown cap work. That change remains a separate tested financial-control release. Do not claim its source-only checks operate on the VPS or ship it accidentally with a UI milestone.

`0032_live_readiness.py:83–107` rejects enabling a live policy without explicit acknowledgment and an owned passing forward report. Live intents require governed lineage and a protective stop. Service-level readiness adds exact strategy/account/connection/source identity, recency and prospective numerical checks. Database guards are defense in depth, not an independent mathematical certification of a report.

### Authentication and request integrity

`auth.py:17–19,60–100` uses Argon2id through the pinned Argon2 default PasswordHasher and an equal-cost dummy password verification for unknown users. It should not be described as making the entire login path constant-time. Login throttling locks a persistent database row across API processes; owner lookup is also locked. `auth.py:31–49` checks exact configured Origin and uses `secrets.compare_digest` for CSRF verification on mutations. Session tokens are random, stored only as hashes and checked for expiration/revocation.

`app.py:98–114` omits raw validation inputs and database exception bodies from public errors. The new activity and observation endpoints require the existing owner authentication; POST observation also inherits Origin and CSRF checks. Tests cover unauthenticated access, bounded limits, invalid cursors, owner isolation and private-payload omission.

### Secret handling and recovery packages

`scripts/tests/test_server.py:52–66` verifies diagnostic redaction for raw and URL-encoded secrets, Gateway tokens and owner passwords. Its release/source tests require allowlisted files, commit-bound image identities and rejection of known private fixtures. `release_bundle.py` creates the public server package; the separate private recovery archive intentionally includes credentials and must retain private permissions. Redaction fixtures are evidence for covered encodings, not a guarantee against every possible secret representation.

## 4. Frozen quantitative result and new scanner semantics

The registered EUR/USD 15-minute trial remains failed, as recorded in `VALIDATION_RESULT_2026-10-08.md:7–21` and `ACCEPTANCE_PLAN.md`. Separate holdout coverage was 1,730 bars against 1,500 required; drawdown was 1.4902% against a maximum 10%. Closed trades were 16 against 20 required. Baseline net return was −1.4614%; higher-cost return was −3.7942%; both required at least zero. There were zero winning holdout trades. The research account remains halted; no qualification or prospective protocol was requested for the failed strategy.

The 14-day/20-trade prospective gate and existing financial controls remain unchanged. Declared slippage/financing assumptions and closed-bar replay are not measured broker execution costs.

`multifactor_v1` is a versioned setup-rule score, never a win probability. It uses Decimal precision 120, trailing closed bars, confirmed two-bar pivots, complete higher-timeframe EMA history and as-of calendar receipts. Missing factors receive zero without renormalization and prohibit the high band. Unknown/stale/incomplete macro coverage incurs the same 0.20 penalty as a nearby event. Structural clearance and illustrative 2R/3R targets are distinct; no invented position size or execution permission is displayed.

Old profiles retain `legacy_v1`. New profiles explicitly select scoring and optional multiple timeframes/catalog slices. Daily scanning is supported but weekly/monthly trend context is unavailable in the chart provider contract and remains visible as incomplete. Dynamic US listings remain unclassified for native research until an owner selects stock/ETF identity. Catalog counts are provider/account observations, not fixed 68/13,000 promises.

Pure detection/scoring makes no model calls. Tests run a full 50-instrument synthetic scanner with external HTTP forbidden, verify future-bar invariance, exact score thresholds, short/long geometry, multi-timeframe identities and duplicate reuse. Software correctness does not establish statistical edge; freeze a research protocol and evaluate unused chronological evidence separately.

Shared read-only candle budgets persist across processes/restarts and honor 429 cooldowns. OANDA burst 50/refill 50 per second is below its documented 120 requests/second ceiling; persistent clients reduce new connections. Alpaca burst 20/refill 3 per second bounds a rolling minute to 200 requests. Neither budget creates a paid feed entitlement or governs the separate broker transport. Official references checked 9 October: [OANDA development guide](https://developer.oanda.com/rest-live-v20/development-guide/), [Alpaca market data subscriptions](https://docs.alpaca.markets/us/docs/about-market-data-api).

## 5. OpenClaw authority, visibility and remaining ownership runtime

The release's registered project-action tool invokes `openclaw_api.py` → `assistant.execute` → deterministic scanner, research, governance and paper services. It does not expose a broker HTTP client, broker credentials or a direct broker-order tool. The separately deployed open research tools are read-only and keep product identity, coverage limitations and native-report authority separate. Source text is evidence, never instructions.

The new opportunity brief reads saved high-score candidates and stores immutable assistant request/completion receipts. It does not rescan, qualify, approve, size or execute a trade. Candidate paper staging binds the same native artifact, immutable strategy deployment, matching backtest and current governance decision; the existing paper service rechecks account/pause/policy/quote/allocation facts. Repeated requests reconcile the original plan, and changed bindings reject reuse.

The feed projects authoritative jobs and receipts rather than writing a competing activity state machine. It verifies immutable checksums, uses bounded keyset pagination and safe field projections, distinguishes OpenClaw/background/owner/unknown actors and follows actual asynchronous results. A success flag without its artifact is blocked; stale running work and uncertain submissions remain visible. Current work shows the most recent 16 active records; history is bounded to 1,000 loaded records and can include additional active work. Loaded older unfinished jobs are refreshed by identity. Scan activity becomes visible after its transaction commits; live per-instrument scan progress is not available.

Full ownership still requires the five capabilities and acceptance scenarios in `OPENCLAW_PROJECT_OWNERSHIP.md:29–73`: shared durable goal lineage, leases/heartbeats/wake/resume and bounded recovery, isolated general engineering with actual test execution, governed release/operations tools, and owner pause/cancellation/direction. The dashboard improves visibility into existing services; it does not implement that companion runtime.

## Verification record

Post-installation checks on the first immutable release (`1f5f00b`) exposed a native-artifact checksum mismatch in the activity projection: `analysis.finish` uses sorted JSON with default separators, while compact assistant receipts use `paper.checksum`. The historical native report matches its original writer; changing its stored checksum would be incorrect. The correction verifies the exact serializer for `AnalysisArtifact`, preserves strict receipt verification and exercises the actual native writer in the asynchronous activity regression. The final correction and live feed results belong in the release verification record.

The initial production preservation guard also detected routine valuation observations after workers restarted. All 2,844 pre-backup readiness records matched their original count/hash, and the additions were checksum-valid automatic valuation observations. The 33 other protected authority/settings tables, private credentials, non-image settings and all 23 execution-critical files were unchanged. Treat append-only monitoring observations separately from changes to historical evidence or financial authority.

Before this report: 47 baseline stop/reprotection/readiness/discovery tests and 11 deployment controls passed. The focused scoring/activity/calendar suite passed, and both built-UI browser workflows passed on disposable PostgreSQL with explicit synthetic candles, no real model calls and no broker orders. The governed candidate-to-paper test covers original evidence, mismatched inputs and stable replay.

Release validation results, exact counts, immutable commit/images, backup/restore/migration results, authenticated endpoint checks and post-deployment source/settings comparison are recorded in the accompanying release verification document after they actually complete. Any missing operational acceptance remains explicitly open.
