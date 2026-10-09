# Trader dashboard and OpenClaw activity implementation

Authorized 9 October 2026 (Europe/London). This is the implementation specification, not a claim of deployed behavior or strategy performance.

## Evidence baseline

The read-only IONOS check on 9 October found 15 healthy services on `0033_worker_progress`, with 77 tables. Application image identity is `18b3d34473b666cfa83df6679357481bd4c504bb`; the subsequent `7ee38c9` ownership instruction supplement does not implement autonomous ownership. Pre-existing local research changes require `0034_quant_lab`. This milestone requires `0035_scanner_budget`: it preserves sequential migration 0034 as a compatibility table without installing the separate Research Lab UI, APIs or engines, then adds shared read-only budgets and widens assistant operation names. Evidence is `artifacts/trader-dashboard-baseline-host-20261009.json` and `artifacts/ownership-deployment-7ee38c9.json`.

The baseline FX stop, Alpaca reprotection, live readiness/valuation and instrument discovery checks passed 47 tests. Deployment controls passed 11 tests. Fixture protection recovery is not a connected broker lifecycle. The frozen EUR/USD trial still failed trade count and returns; coverage and sampled drawdown passed. No financial setting, pause, allowance or qualification is changed by this work.

Reuse `instrument_discovery`, `intelligence_catalog`, `scanner_discovery`, `candidate_pipeline`, `candidate_research`, immutable assistant receipts and authoritative service jobs. Do not construct a replacement scanner or duplicate authoritative job state in an activity table.

## Versioned setup scoring

Existing profiles default to `legacy_v1`, preserving saved evidence and scoring behavior. New profiles may explicitly select `multifactor_v1`. Scores measure observable setup rules, not expected returns, win probability or execution permission. Display `Setup score: 88/100`, with high/moderate/low score bands at exact decimal thresholds 0.82 and 0.65. Keep original decimal score, component contributions, algorithm version, parameters and source receipts in immutable candidate evidence. Do not recompute historical candidates under new rules.

Use Decimal precision 120 for indicators and geometry. A float may be a presentation projection; classification uses the stored decimal value before display rounding. Financial values use decimal strings.

For a candidate with last closed bar at T:

* Pattern (0.20): positive distance beyond the detector's prior reference divided by prior 20-bar ATR, clamped to [0,1]. The existing family detector remains the prerequisite.
* Activity (0.25): current activity divided by the prior 20-bar mean, divided by 2.5 and clamped. Preserve FX provider-tick, selected equity-feed-share and crypto-base-volume semantics.
* Trend (0.25): direction alignment on higher closed bars: close against EMA50 and EMA50 against EMA200. EMA uses a period-length SMA seed. Contexts are 1m→5m/15m, 5m→15m/1h, 15m→1h/4h, 1h→4h/1d, 4h→1d. Daily scanning remains supported but higher weekly/monthly context is unavailable in the existing provider contract; this must be visible, not fabricated.
* Squeeze (0.15): 20-bar Bollinger width (population standard deviation, two deviations on each side). The previous width must be in the lowest fifth of its preceding 20 widths; current width must expand at least 1.2 times. Expansion saturates at 1.5 times. All windows are trailing closed bars.
* Geometry (0.15): latest confirmed two-bar pivot for invalidation and nearest confirmed opposing pivot for structural clearance; score clamps structural reward/risk divided by 2.5. A pivot is unavailable until both confirming bars have closed. Long FX entries use ask and stop lows use bid; shorts use bid and stop highs use ask. Structural clearance is separate from illustrative 2R/3R targets. Geometry is research-only; executable sizing still belongs to the governed plan service.
* Macro: deduct 0.20 for a relevant scheduled event within ±30 minutes. Missing, future-received, incomplete or stale calendar coverage is unknown, deducts 0.20 and never receives a clean label. Calendar acquisition uses the existing read-only economic adapter outside candle scoring and persists source coverage separately.

All price bars must be closed at or before T. Calendar coverage must have been known by T. Record the distinction between candle time and receipt time; receipt-observed history is not proof of historical availability. Missing required components contribute zero without renormalizing. Incomplete evidence caps the final score below 0.82, so the high band always requires complete required evidence. Invalid geometry has no suggested stop or structural R:R. No invented position size or policy budget is displayed.

## Universe and multi-timeframe contract

Keep legacy `interval`; optional distinct `intervals` expands it without rewriting old profiles. Catalog selection refers to supported, available account instruments, not fixed counts. Reuse provider catalogs and preserve stock/ETF classification provenance. Dynamic discovery must not automatically register a listing as an ETF or buy a subscription.

Resolve and checksum the cycle's catalog membership. Process a bounded, resumable slice; expose total universe, processed count, coverage, deferred count and request budget. Apply provider/feed-specific caching, persistent connections and backoff outside pure detection. Advance slices after rate limits or restarts without starving symbols. A full catalog is not described as fully scanned until every member's coverage is recorded. Deduplication includes instrument, timeframe, family, direction and closed-bar identity; changing context must not duplicate downstream research or orders.

The shared candle budget uses OANDA burst 50/refill 50 per second and Alpaca burst 20/refill 3 per second. These are application ceilings, not purchased entitlements. [OANDA documents 120 REST requests/second and two new connections/second](https://developer.oanda.com/rest-live-v20/development-guide/); [Alpaca documents 200 historical requests/minute for Basic](https://docs.alpaca.markets/us/docs/about-market-data-api). The prompt's OANDA 100/minute figure is not its documented limit. Connection reuse reduces new connections; this milestone does not replace broker transport with the market-data budget. Per-symbol price/activity prefilters still apply after retrieval; catalog size is not a claim of liquid, timely or research-ready coverage.

## Dashboard and governed actions

Add a Trader dashboard destination and dockable OpenClaw activity panel. Keep the workstation's existing fonts and palette. Use a compact evidence rail for score factors and a chronological work timeline, with responsive stacked layout and keyboard-accessible details. Show source timestamps and incomplete evidence beside the score.

Opportunity controls include asset/timeframe/score filters, chart, watch and existing research admission. Paper staging shows prerequisites, requires an eligible governance decision and matching backtest/native evidence, and invokes existing plan services. It never generates an approval or creates a direct broker path. Stable request identities survive uncertain responses and repeated clicks.

## Activity feed contract

`GET /api/v1/assistant/activities` is authenticated and owner-scoped, with bounded limits and deterministic keyset pagination. It projects existing assistant receipts, chat turns, scans, candidates, native/quant research and governance jobs, plus relevant append-only audit events. Check stored checksums before exposing immutable payload projections. Never return credentials, private prompts, raw provider errors, entire tool parameters or arbitrary result dictionaries.

Each item includes source identity, timestamp, last observed status, actor, category, title, summary, resource links and bounded receipt details. Actors distinguish OpenClaw, background workers and the owner; unknown attribution stays unknown. States include queued, running, waiting, blocked, retry scheduled, reconciling, review required, completed, failed and cancelled. Source job state is authoritative: a submission receipt remains waiting while its linked job runs. Missing completion or stale heartbeat cannot prove success.

Show current work separately from history. Refresh automatically while visible, avoid overlapping requests, retain the last successful view during an outage, and visibly mark stale data. Filters describe loaded coverage and support fetching older pages. Completion reflects the individual service operation, not automatic satisfaction of a parent goal or trading acceptance.

## Ownership companion and acceptance

Keep general engineering/research/operations capability in scope. The feed must not claim that orchestration exists simply because it displays receipts. Full ownership requires durable goal lineage, stable side-effect identities, leases, wake/resume, bounded recovery, owner pause/cancellation and an engineering environment separated from production financial credentials. Its acceptance remains that in `OPENCLAW_PROJECT_OWNERSHIP.md`.

Checks must cover future-bar leakage, pivot confirmation, long/short symmetry, missing/stale macro evidence, threshold boundaries, offline zero-model scans, cross-timeframe deduplication, catalogue/backoff/resume behavior, old-profile preservation, receipt integrity, owner isolation, pagination ties, actual asynchronous outcomes, governed action prerequisites and responsive browser workflows.

Quantitative validation is independent of software acceptance: freeze the scoring version, retain rejected trials, compare against simple baselines and evaluate on unused chronological evidence. Passing software tests does not establish a profitable scanner.

Release only through matching immutable source/images, verified backup/restore, migration rehearsal and post-installation checks. Record implementation, test, research and deployed status separately. No live activation or allowance change is authorized by this dashboard release.
