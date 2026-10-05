# Crypto scanner verification

This records the original crypto milestone. Current scanner profiles also support Forex and stocks/ETFs and no longer require a strategy. See [independent scanner profiles](INDEPENDENT_SCANNERS.md) for the current setup, identity, optional links and upgrade checks.

Recorded 3 October 2026 for development 0.7.0. This adds a working manual crypto spot scanner and explicit research escalation to the existing workstation. The full platform is not ready for server installation.

## Working slice

Settings → Scanners saves append-only profiles tied to an exact immutable strategy version. The owner selects a supported instrument universe, candle interval, prior-bar lookback, minimum exchange-base volume ratio, candidate lifetime, AI route and daily allowance for newly admitted scanner research jobs. Save and page refresh initiate neither a scan nor AI work. New installs have no profiles, thresholds or enabled scanners.

`closed_bar_breakout_v1` passes only when the latest closed candle's close exceeds every high in the configured prior closed-bar window and its volume reaches the configured ratio to the average volume of that window. Forming candles are excluded. Fresh snapshots, enough closed bars and gap-free evidence are required. Missing/zero reference volume fails the filter rather than counting as a pass. Prices and features use Decimal arithmetic; this detector is screening, not a profitability claim or trading signal with execution authority.

Each immutable candidate preserves provider, instrument/venue, interval, detector version/parameters, exact closed-bar evidence, snapshot ID/checksum, receipt/provider times, reasons and derived features. Equivalent evidence can match several strategies; append-only matches retain every strategy/profile version and its expiry. Detector parameters and instrument/timeframe are part of the context key. Repeated scans do not renew the same match's lifetime. Changed/disabled profiles make older matches inactive. Expired and ignored candidates cannot request research.

The workspace Scanner panel provides Crypto plus visibly unavailable Forex, Stocks, Small Caps and Events families. Candidates have watch/ignore actions and reasons, original-evidence details, matching strategy selection, history, native job/report links and chart instrument/interval linking. Recent run results show matched, filtered and unavailable instruments. Chart linking opens the current chart; detected historical overlays/replay are not implemented.

## Research admission

Request daily research is explicit. It uses the existing native queue and the unchanged four analysts/debates, never a second LLM scanning brain. The candidate and strategy/profile checksums are retained as job metadata. Scanner features are **not injected into the native graph**; the current native provider path remains daily USD research while Binance observations are USDT spot.

Research reuse requires equivalent candidate evidence, model endpoint/models, saved connection revision, effective analysis budgets, UTC research date and upstream graph revision. Independent evidence/configurations do not share a job. Equivalent matches can reuse one job while retaining separate strategy links. Failed/interrupted work is retained and reused as that outcome; there is no silent retry. Explicit reruns through the ordinary Tauric panel remain separate owner actions.

A per-strategy-version UTC-day allowance reserves a count for each newly admitted scanner research job, including jobs that later fail or are interrupted. Reuse does not charge another count. Profile edits do not reset the counter. The existing global queue limit and single native worker still apply. This is an admission-count limit, not a token or monetary spending guarantee; ordinary manually requested research is outside this scanner allowance.

Admission and profile/run changes serialize in PostgreSQL. Connection and effective analysis settings are locked through atomic research admission. Immediately before launching the native subprocess, the worker checks current candidate eligibility, immutable evidence/profile checksums and connection revision. Expired, ignored, superseded or changed-connection work fails without starting a model request. Work already started can finish after a candidate expires; the report is retained, and its candidate remains expired. A scanner candidate never grants governance permission, sizes an order or executes a trade.

## Evidence

| Check | Result |
|---|---|
| Backend suite | 140 checks pass, including 20 scanner cases |
| Scanner browser workflow | Six checks pass using explicitly synthetic candles in a disposable database |
| Built Linux-container workflow | Five checks pass: migrated readiness, profile/candidate/history persistence, owner/CSRF protection, private ingress, desktop/mobile rendering |
| Production build/types/lint | Pass |
| Actual Binance observation | Fresh BTC/USDT 15-minute snapshot; 299 closed candles, zero gaps; temporary verification parameters returned filtered because both filters did not pass |
| Broad screening model calls | Zero |
| Broker order calls | Zero |
| Real-owner financial/scanner setup | No paper balances, allocations, strategy versions or scanner profiles seeded |

The scanner backend cases cover exact request identity, conflicts, history/SQL immutability, owner boundaries, forming bars, missing volume/gaps/outdated feeds, shared strategy matches, differing detector contexts, watch/ignore, expiry without renewal, budget exhaustion/edits, model/worker availability, concurrent admission, equivalent research reuse, connection rotation and expired queued work failing before a model subprocess starts. Synthetic fixture checks do not claim live-market setup quality.

## Remaining scanner work

Automatic cadence/scheduling, configurable prioritization/ranking, OpenClaw analysis dispatch, a shared macro/theme research cache, detector families, FX/stock/small-cap/event feeds, futures features, adaptive quote/tick/depth subscriptions, point-in-time replay, chart overlays and scanner outcome/token analytics remain pending. Future families should extend these candidate/profile/admission records and shared provider infrastructure. No universal scoring formula or hardcoded strategy-name branch is introduced.
