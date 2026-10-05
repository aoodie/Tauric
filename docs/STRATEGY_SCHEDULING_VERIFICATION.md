# Scheduled paper strategy monitoring

Development 0.10.0, verified 3 October 2026. Executable `closed_bar_breakout_v1` deployments can now evaluate closed candles on saved schedules and detect exits for their attributed paper positions while the browser is closed. Monitoring has no proposal, model-review, order or fill authority. Entries still use the existing review/sizing workflow; exit signals require an explicit owner close.

## Owner workflow

1. In **Paper trading**, select a deployment pinned to an executable strategy version.
2. Under **Monitor**, enable scheduled monitoring, choose a check frequency and record a reason. The allowed range is 30–3,600 seconds, capped by the rule candle interval and signal lifetime. At most 20 monitors may be enabled. New installations and existing deployments start without schedules.
3. Inspect service readiness, next check, last outcome and errors. Background results appear in the existing strategy rule result/history with saved candle evidence, ledger/control context and signal expiry.
4. Use the existing actions to propose/review/size an entry or explicitly close a current attributed exit signal. Account halt/deployment pause block new-entry eligibility while an existing position can still produce an exit signal.
5. Disable the schedule to stop all background checks for that deployment. **Load monitoring history** shows saved configuration changes and check receipts. Disabling or changing a schedule preserves prior history.

## Recovery and concurrency

The independent `strategy_scheduler` service runs one evaluation at a time. Owner locks follow the existing evaluation/action order; competing workers skip locked owners. A scheduled evaluation, its immutable receipt and its next due time commit in one transaction. Deterministic request IDs and the unique version/due-time receipt constraint prevent two committed checks for the same due slot.

If a worker dies before commit, PostgreSQL rolls back that check and releases its locks. A restarted worker finds the still-due schedule and evaluates current evidence. If it dies after commit, the saved next due time remains authoritative. Multiple missed intervals coalesce into one current check; the worker does not replay old candles or place catch-up trades. After completion the next check is due one configured cadence later, so slow feeds do not create an expanding queue.

Feed outages, rate limits and invalid/stale evidence produce saved BLOCKED evaluations. Other evaluation failures produce sanitized failed-check receipts. Storage failures leave the due check pending for reconnect. Heartbeat older than 60 seconds displays the service as unavailable. The browser polls saved results every five seconds; monitoring continues independently of browser sessions.

Migration `0014_strategy_schedules` adds append-only configuration versions and check receipts plus a mutable scheduling cursor. Database guards enforce owned executable deployments, contiguous revisions, current-version cursor ownership and linked evaluation lineage. The application role cannot update/delete/truncate configuration or check history. Saves require expected revision and request ID; conflicting writes fail. Existing strategy runs now accept a scheduled context and optional enclosing transaction without changing manual requests or entry/exit validation.

## Verified evidence

- All **20 dedicated scheduling checks** pass: bounds, authentication/CSRF, ownership, manual-route rejection, retry/conflict handling, immutable history, count limits, database reference guards, current-evidence recovery after an interrupted transaction, competing workers, provider/runtime errors, heartbeat expiry, paused-entry blocking and attributed exit monitoring during halt/pause.
- All **256 backend regression checks** pass; the optional browser check is run separately.
- The built browser workflow passes **eight recorded checks**, including enabling monitoring, background entry/exit signals, reviewed paper entry, reload persistence, explicit attributed close/reconciliation, disable/history and mobile layout without application errors. These candles are explicitly synthetic. Evidence remains in `artifacts/strategy-rules-browser-results.json` and the strategy-rule screenshots.
- Built Linux arm64 API/web/monitor services completed **two scheduled checks on real public Binance BTCUSDT 1-minute candles** in a disposable database. The first result was HOLD with snapshot `40a7b8a3-e6b6-41a9-8113-efb51db526c3` and run `32873ab2-1507-4e61-a363-9ba992382812`. Stopping the worker across its next due time and restarting produced one subsequent evaluation. The API restart preserved both evaluations/receipts and the owner session. Disabling removed the next due time. The account's existing policy records stayed unchanged; no proposals, decisions, plans, fills, model calls or broker calls were made. Evidence is in `artifacts/strategy-schedule-container-results.json`.
- Frontend type checking/production build and touched-source lint/format checks pass. The new worker images were built; a private primary database backup was saved before migration. The local migration applied successfully and the updated API, web, native/governance/chat workers and strategy monitor reached readiness. Primary paper-account/configuration/journal fingerprints matched before and after the upgrade, with no seeded schedules, scheduled checks or strategy evaluations. The disposable connected-check containers, database and temporary credentials/key were removed.

Reproduction and upgrade instructions are in [Development](DEVELOPMENT.md). Configuration and periodic-check history APIs are under `/api/v1/strategies/deployments/{deployment_id}/schedule`; existing paper state includes monitoring configuration and service status.

## Limits and next milestone

Monitoring checks complete candles periodically. A forming candle cannot establish a signal. Downtime or unavailable evidence can miss earlier intrabar or historical triggers; this is not a broker protective order, a continuous tick stream or a guarantee of execution at a stop/target price. Real connected exit detection has not been separately observed; its attributed mechanics are verified with synthetic candles and paper fills. The connected built-worker check establishes public-feed evaluation and restart persistence.

Automatic scanner scheduling, autonomous paper dispatch, richer strategy families, broader instruments/products and live broker adapters remain pending. Long-running load/soak, backup restoration, Linux server release and installation readiness remain separate gates. The next execution milestone is autonomous paper dispatch under configured account/deployment policies.
