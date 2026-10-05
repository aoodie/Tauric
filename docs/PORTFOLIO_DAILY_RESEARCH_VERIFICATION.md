# Daily portfolio research coordinator

Implemented 3 October 2026 as a bounded research-only portion of Stage 3 in the [implementation plan](PORTFOLIO_IMPLEMENTATION_PLAN.md). The broader qualification, promotion, automatic recovery and multi-asset execution system remains in progress.

## Available workflow

Choose a portfolio in **OpenClaw chat**, then expand **Daily opportunity and strategy research**. The same controls are available in **Settings → Trading**. Save the mandate first, choose research asset families and registered instruments, select existing scanner profiles and add experiments using owned saved datasets. Configure local daily time, IANA timezone, optional additional cadence and allowed event names. Automation remains disabled until the owner saves an enabled policy. A manual run uses the saved policy; it does not enable its schedule.

Daily work runs in a separate coordinator process with the browser closed. Schedules survive restarts. Missed ticks coalesce into the latest due run rather than replaying every missed day. A repeated daylight-saving hour uses its first occurrence; a skipped time moves to the first valid local minute. At most one run per portfolio and twenty across the coordinator may be active. A waiting native job does not block another portfolio's due work. Native waits have an explicit deadline; timeouts produce a partial digest and no paid retry request.

Both tracks append durable evidence:

- **Opportunities:** existing deterministic scanners record observed bars and findings. Current candidates/watch items are shortlisted by observed volume ratio. This is a screening proxy, not a prediction of net return. Compatible current daily native market research may be reused or admitted within the saved allowances.
- **Strategies:** explicit hypotheses and exact datasets run through the existing costed SMA or saved-version breakout replay. Optional SMA neighbour exploration tests only valid adjacent fast windows. No arbitrary model-generated code executes. Saved implementation sources, dataset checksums, parameters and assumptions are retained. Exact compatible replays are reused. Experiment limits record truncated work as deferred. Comparisons share dataset, capital, risk and cost assumptions and follow the portfolio's saved ranking objective; they do not establish out-of-sample performance.

Each finished or cancelled run saves an immutable digest with both tracks, replay references, original native job references, deferred work, errors and whether missed-run recovery occurred. The workspace exposes these digests and opens original evidence. OpenClaw receives them in its trusted bound portfolio context. This is durable storage and in-app visibility; Telegram/Discord portfolio routing and proactive outbound digest delivery are not implemented.

An explicitly requested chat run uses `tauric_request_portfolio_research`, whose portfolio comes from the active leased conversation. `tauric_portfolio_action` can edit the bound research policy when the saved chat editing permission permits it. Research policy edits use their own revision and append an atomic before/after action receipt. The model cannot supply another portfolio target or change the shared allowance, financial risk limits or its own permissions. General chats and direct messages cannot invoke a portfolio run without a portfolio binding.

## Admission accounting and native dependency

The owner can configure a **Shared daily AI job allowance**. It covers new native analysis and governance admissions across their existing routes, including failures. The portfolio also has a native job allowance per UTC day. Positive automatic native allowance requires an explicitly saved shared allowance. Zero permits compatible reuse but blocks new covered jobs. Unconfigured shared allowance preserves existing manual routes and their limits. Existing chat/scanner limits still apply. OpenClaw chat replies have their own settings.

This is admission accounting, not measured token usage, a currency ceiling or a guarantee about provider costs. Unknown usage remains unknown. Cache compatibility includes current date, instrument, model route fingerprint, effective native settings and upstream revision. Pending admissions are checked for source validity. Failed/interrupted compatible daily work is readable and reused without silently billing a retry. Original native job inputs and artifacts remain unchanged when referenced from another portfolio.

The coordinator and its tools cannot qualify/promote/deploy strategies, approve proposals or submit orders. Daily native market context does not assess an exact intraday scalp setup. The separate [exact-version research-comparison qualification](STRATEGY_QUALIFICATION.md) workflow is now implemented; daily automatic qualification of these experiments remains pending. The existing automatic paper entry path retains its original-evidence, freshness, native `BUY`, review and financial gates. Historical manual paper workflows with optional native evidence retain their previously documented behavior; this coordinator grants them no new authority. Protective exits retain their existing deterministic path.

## Asset scope

Crypto spot BTC/ETH/SOL remains the working scanner and daily native market route. Read-only OANDA FX and Alpaca US stocks/ETFs now support charts and explicit-cost retrospective SMA experiments; connected feed verification requires credentials and entitlements. Capability records separately disclose implemented data/replay, configured feed access, screening, native research and live execution. Selecting an unavailable research route records deferred work; it never substitutes crypto. Crypto futures, exchange futures and IBKR adapters remain planned. All live execution readiness flags remain false.

The new adapters preserve FX bid/ask and tick-count semantics, exchange calendars, selected stock feeds/split adjustments and explicit research financing/commission assumptions. Historical FX financing/holiday multipliers, stock dividends/corporate-action cashflows, point-in-time universes, borrow and execution qualification remain outstanding. See [asset data adapters](MARKET_DATA_ADAPTERS.md).

## Validation

Disposable PostgreSQL fixtures verify strict policy validation, ownership, optimistic concurrency, retries, immutable history, timezone transitions, coalesced missed runs, both actual research tracks, exact replay reuse, unchanged trading records, shared/per-portfolio admission caps, cross-portfolio reuse at zero allowance, failed-job reuse, cancellation and native admission rejection after mandate changes, event allowlists, and scoped chat action receipts.

The built frontend walkthrough uses isolated storage, a real coordinator and offline market fixtures. It verifies saved schedules, both tracks, portfolio isolation, evidence links, history, responsive layout and completion of a missed daily run after the browser closes. Pinned OpenClaw SDK build/manifest validation and network-disabled plugin/configuration tests verify trusted factories, invocation guards, the seventh portfolio research tool and unchanged governor isolation. No real model requests or trades are required by these checks.

The local preview is upgraded only after a private database backup has been restored into a disposable database and verified. No owner portfolios, research policies, allowances or automation switches are seeded by deployment.

Final evidence: the full suite passed **320 tests**, including all three optional browser workflows. After the final scheduled-job chat-read correction, **40 affected portfolio/chat tests** and the daily browser workflow passed again. Frontend build/format, Ruff, pinned plugin build/manifest validation and all three network-disabled plugin/configuration fixtures passed. The private pre-upgrade backup restored at schema 0016. The local preview is now at schema 0017 with healthy API, Gateway and research/strategy workers; owner research policy, allowance and run counts remain zero, and native/review queues are empty.

Evidence: `artifacts/portfolio-research-preview-verification.json`, `artifacts/portfolio-research-backup-verification.json`, `artifacts/portfolio-research-browser-results.json` and desktop/mobile screenshots.

## Asset adapter follow-up — 3 October 2026

Read-only FX and stock/ETF adapters and calendar-aware explicit-cost SMA experiments are now implemented. The initial registry exposes six instruments; owner-supplied dataset experiments can use them independently of connected feeds. Readiness separates implemented data/replay from configured feed access, pending scanners and pending native assessment/execution. Existing portfolio experiments use the same research service and retain explicit costs. Credentials/entitlements and real connected verification remain outstanding. See [asset data adapters](MARKET_DATA_ADAPTERS.md).
