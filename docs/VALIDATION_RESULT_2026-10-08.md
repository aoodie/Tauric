# Tauric validation and acceptance result — 8 October 2026

The supplied 7 October gap analysis was partly stale. Its central conclusion remains valid: Tauric has not established prospective strategy performance or a complete unattended governed broker lifecycle. Deployment, several correctness fixes and worker monitoring have advanced further than the report described.

## Research decision

The unchanged EUR/USD 15-minute breakout **failed** the registered screening criteria. No threshold or parameter was changed after observing the results.

| Measure | Frozen requirement | Observed holdout |
|---|---|---|
| Separate holdout coverage | At least 1,500 bars | 1,730 bars — passed |
| Closed trades | At least 20 | 16 — failed |
| Net return after baseline costs | At least 0% | −1.4614% — failed |
| Net return under higher costs | At least 0% | −3.7942% — failed |
| Sampled drawdown | At most 10% | 1.4902% — passed |

Training returned −1.9183% across 41 closed trades. The holdout had zero winning trades. The trial used 5,000 OANDA midpoint/bid/ask bars, 3,250 training bars, a 20-bar embargo and a separate 1,730-bar holdout ending before the previously viewed diagnostic window. Financing and slippage are declared research assumptions; they are not measured historical broker costs. Closed-bar replay does not establish intrabar broker execution parity.

The failed result remains in immutable records on IONOS: protocol `29781c2e-ee38-4897-9d1f-7c55f826be0c`, evaluation checksum `91fc974738452c5bbd4a6c23fa7516c061010c95fb8309a7d6b00e69fe9a3f76`, report `024ae0c0-d882-43d2-af39-b3921ec03b43`. The research account remains halted. This trial sent no broker orders and made no model calls.

Numerical prerequisites failed, so no exact native qualification assessment or prospective protocol was requested. This strategy needs a stated new hypothesis and a new registered trial using unused evidence before prospective promotion. The 14-day/20-trade prospective gate remains unchanged.

## Engineering changes

[Draft PR 6](https://github.com/aoodie/Tauric/pull/6), candidate `18b3d34473b666cfa83df6679357481bd4c504bb`, adds durable worker progress without changing order authority. Trading shows completed work, idle successes, failures, stale observations, queue age and unresolved orders. Error categories and invocation IDs omit private exception bodies. Monitoring storage failures do not block protection or reconciliation. Migration `0033_worker_progress` adds one bounded progress table. The execution signature now binds both evidence workers and the progress implementation.

The candidate preserves the separately published assistant upgrade `363fd9b7c1acbf443d361330db72f8c56c39d49c` and uses its own release branch. The initial full local suite found one missed schema-readiness constant: 1,003 tests passed and that readiness test failed. The constant was corrected; the readiness and monitoring rerun passed all six tests. The earlier focused suite passed 18 tests, deployment controls passed 11 tests, and frontend formatting/build plus OpenClaw build/validation passed. [Hosted checks](https://github.com/aoodie/Tauric/actions/runs/37734553752) subsequently passed all 1,004 backend tests, 11 deployment tests and frontend/OpenClaw checks; 20 browser tests remain deselected. [Hosted release](https://github.com/aoodie/Tauric/actions/runs/37734549950) also passed all four multi-architecture image jobs and clean native amd64 installation/restoration of schema 0033, 77 tables and four runtime volumes. The operating bundle is assembled from files individually verified against that published commit and its hosted image digests; direct connector ZIP delivery returned HTTP 403. Completed deployment results are recorded below.

## Backup and host evidence

A fresh IONOS backup restored all 76 database tables and four runtime volumes with matching hashes, contents, permissions and ownership. Only the isolated database and read-only diagnostics ran; no restored trading worker started. The owner explicitly approved encrypted workstation recovery copies, including the fresh pre-upgrade backup of the then-current 363fd9b server. Its decrypted checksum matches the server archive, and no plaintext was saved locally.

Initial backup SHA-256: `23bbf727ba4a25bf4d8cdc2bed918c1ac4bc9f55cd251a05031b0f4ce4555fc2`. Fresh immediate pre-upgrade backup SHA-256: `65d0d0692b4a83f25924bfcd911fa4b7b38004d0ab412f827a48f66e8032a217`; its full restoration and encrypted off-host decryption/hash checks also passed. Evidence is saved under ignored `artifacts/acceptance-preupgrade-evidence-20261008.json`; the encrypted archive and recovery key remain in the existing private backup folder. Recovery copies are retained without automatic deletion. Scheduled off-host backup and retention remain unconfigured.

All 15 production services resumed healthy after capture. Public HTTPS trust and IP identity passed verification. The certificate is valid from 6 October to 12 October; future certificate renewal has not yet been observed.

## Original gap findings

| Report claim | Validated status |
|---|---|
| No prospective strategy evidence | Open; this unchanged baseline also failed retrospective screening |
| Nothing has run on IONOS | Stale; installed and serving trusted HTTPS, with reboot and restore evidence |
| No connected unattended broker lifecycle | Open |
| Hosted image publication unverified | Stale for deployed `0e30a3b`; its hosted checks and install/restore passed |
| FX SMA stops use midpoint rather than bid | Fixed in the published baseline; focused stop diagnostics pass |
| Alpaca remainder protection recovery absent | Implemented with replacement protection and recovery; actual connected-cycle proof remains open |
| Open-position valuation absent | Marked valuation and open losses/entry fees implemented; measurement coverage remains a gate |
| AI contribution to returns/costs unproven | Open |
| IBKR and Binance execution incomplete | Open agreed full-release scope |
| No frontend code splitting | Partly stale; market charts load lazily, although the main bundle remains large |
| Model latency against signal lifetime unmeasured | Open in actual connected operation |
| Worker heartbeat can hide lane failure | Monitoring implementation completed; deployment verification recorded below |
| Labels and documents drifted | Several labels fixed; current acceptance documents corrected, historical evidence retained |
| Browser tests excluded from hosted CI | Open; 20 browser tests remain deselected |

Only the interface work explicitly listed in `POST_LAUNCH_TODO.md` is treated as post-launch. Other agreed platform requirements remain release scope.

## Remaining acceptance

Passing research and prospective strategy evidence; a qualifying automatic signal → native assessment → OpenClaw approval → actual protected fill → attributed exit → reconciled journal; representative 24-hour workload and measured latency/storage growth; scheduled encrypted off-host backups and retention; observed certificate renewal; matched AI shadow comparisons; critical browser CI; remaining broker and platform coverage.

Live-money activation remains subject to its existing qualification and account gates.

## Completed deployment verification

Candidate `18b3d34473b666cfa83df6679357481bd4c504bb` is deployed on IONOS, preserving the prior assistant upgrade. All 15 services are healthy on schema `0033_worker_progress`, with 77 tables. All four running application images use the hosted immutable digests and matching source labels.

The injected post-migration startup failure rehearsal passed in isolated projects: existing application tables remained unchanged after migration, and the previous schema, all 76 tables and four runtime volumes restored with matching contents. This proves recovery for this migration and injected startup failure, not general production rollback certification. Immediately before deployment, a fresh backup of the then-current `363fd9b` server also passed full database/runtime restoration without starting recovered trading workers.

Post-upgrade comparison confirmed 26 owner, broker, strategy, qualification, portfolio and account authority tables unchanged, along with all private configuration files. All seven worker lanes reported healthy with zero failures. Broker and prospective checks were idle; valuation had completed work. Empty queues and idle successful checks do not prove a connected trade.

Trusted public HTTPS, `/health/ready` (200), unauthenticated progress access (401), and delivery of the frontend progress view passed. Actual signed-in HTTP verification remains pending: the existing private login file's password does not match the server owner. The owner record matches the immediate pre-upgrade backup, proving this mismatch predates the monitoring deployment. No password reset was performed. The owner has been asked for the current login file location, without requesting a password in chat.

Evidence: `artifacts/acceptance-deployment-20261008.json`, `artifacts/acceptance-failed-upgrade-recovery-20261008.json`, `artifacts/acceptance-postcheck-20261008.json`, and `artifacts/acceptance-public-boundary-20261008.json`.
