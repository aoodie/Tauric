# Market intelligence and strategy discovery

Owner direction recorded 4 October 2026: focus on market intelligence and strategy discovery. IBKR and Binance/crypto trading remain pending for a later stage. This changes implementation priority, not existing broker policies or runtime settings. No trading, research schedule, numerical budget or paid model call is enabled by this plan.

## Existing foundation and actual gaps

The platform already has independent manual scanners, daily native research, a scheduled portfolio research coordinator, immutable datasets/results, costed SMA and saved-breakout replay, and a bounded crypto exact-version research-comparison qualification path. FX and stock/ETF charts, screening and daily research also exist. Qualification coverage is narrower than data/research coverage and must remain explicit.

Provider discovery and additive selection are now implemented for OANDA FX and Alpaca stocks/ETFs. The nine starter instruments remain available; the owner can search provider catalogs and add selected symbols to the shared registry. Existing scanner and portfolio policy services save research subsets. See [implementation and evidence](INSTRUMENT_DISCOVERY_VERIFICATION.md), including explicit classification and research-coverage limits. Portfolio strategy experiments currently start from owner-authored hypotheses, saved datasets and bounded parameter variants. They do not establish automated strategy discovery. The [market intelligence page](MARKET_INTELLIGENCE.md) now combines saved daily moves, bounded headline metadata, supported policy meetings and currently eligible scanner candidates. Complete economic/earnings calendars, commodity/rates adapters and proposal generation remain unfinished.

## Implementation order

### 1. Research universe

Implemented with bounded per-profile/policy universes; see [verification](INSTRUMENT_DISCOVERY_VERIFICATION.md). Deployment status is recorded there separately. The next implementation stage is market intelligence and opportunity discovery.

Extend the shared instrument registry with provider discovery, searchable selection and saved research universes for OANDA FX and Alpaca stocks/ETFs. Keep stable provider/instrument identities and preserve existing references. Record instrument availability, supported currency/calendar, data coverage and research capabilities separately from execution eligibility. Existing crypto research remains available without requiring crypto execution work.

Acceptance: the owner can find and select instruments beyond the starter list, save a universe and use it consistently in charts, scanners and portfolio research. Unavailable, inactive or unsupported instruments have explicit reasons. Adding a symbol does not imply all native research or qualification paths support it.

### 2. Market intelligence and opportunity discovery

Partially implemented: shared owner-scoped immutable news/policy receipts, manual source refresh, explicit freshness/coverage, personal watchlists and asset-specific workspace tabs. Movers and research candidates remain separate. Forex, stock/ETF and spot crypto use existing price adapters. Commodities and rates show the missing dedicated feeds. See [scope and verification](MARKET_INTELLIGENCE.md).

Add shared, sourced intelligence records for economic releases, central-bank context, company announcements, earnings where source access is available, news and catalysts. Build on existing economic and native research sources. Retain source links, event/publication/receipt times, revisions, coverage gaps and fact-versus-rumour status. Bind records to instruments and themes; deduplicate compatible evidence rather than repeatedly fetching it for every portfolio.

Extend existing scanner profiles with configurable scheduled screening, asset-appropriate detectors and explainable ranking. Produce candidate evidence, relevance to the portfolio, freshness/expiry and reasons to investigate or defer. Freshness and available data constrain the supported research horizon.

The requested XAU/USD weekly brief exposes two remaining coverage gaps: a commodity instrument/data route and a weekly sourced intelligence report. Neither is currently implemented. Gold must retain its own instrument identity and evidence; a gold ETF or daily currency report is not an equivalent substitute. Chat now distinguishes these gaps from the existing daily Forex analysis route.

Acceptance: a selected universe produces inspectable intelligence and a ranked shortlist under saved settings. Source outages, stale evidence and an empty shortlist are visible. Ranking describes its inputs and does not present an invented probability of profit.

### 3. Strategy hypotheses and explicit rules

Extend the existing portfolio strategy-research track to capture proposals from market evidence, templates, owner instructions and Tauric. Each proposal records a falsifiable hypothesis, supporting evidence, target instruments, session/timeframe, entry/exit rules, required features, cost assumptions and expected failure conditions. Produce immutable strategy proposals/versions through existing services.

Generation stays within supported rule families and data requirements. Unsupported ideas remain research proposals with implementation gaps; model-written prose or arbitrary generated code is not treated as an executable strategy. Preserve rejected proposals and variants to record the selection process.

Acceptance: a research run can propose a new hypothesis and explicit supported rules rather than only vary an owner-entered experiment. Its evidence and portfolio context are inspectable. Repeated requests reuse the original proposal/run, and proposals cannot submit orders or modify deployed versions.

### 4. Experiments and qualification

Use existing dataset, replay and qualification services. Expand supported rule and asset coverage deliberately. Freeze rules, parameters, data splits and costs before evaluation; preserve all trials, prior holdout exposure and failed results. Add walk-forward and cost/parameter stress comparisons where supported. Show net returns, drawdown, sample coverage, uncertainty and limitations using defined metrics.

Extend exact-version Tauric assessment for the selected research routes. Distinguish numerical research comparison from eligibility for shadow, simulated paper, broker demo or live trading. Intraday/scalping requires verified spread, delay and fill assumptions appropriate to that horizon; daily reports and candle-only results cannot certify those properties.

Acceptance: a proposal can become a reproducible experiment and a passed/failed/incomplete research qualification report with preserved lineage. Results cannot silently retune held-out data or substitute a daily rating for exact-version assessment.

### 5. Discovery workspace and daily digest

Partially implemented: the market intelligence workspace links original observations, source receipts, current candidates, charts and saved research. Contextual OpenClaw actions prepare an editable draft. Strategy proposals, research comparisons and an extended daily digest remain future work.

Expose research universes, intelligence, opportunities, proposals, comparisons and original reports through the existing workspace and portfolio chat. Extend the current daily digest with findings, evidence, rejected ideas, deferred work and measured usage where available. Chat and Settings use the same versioned research services and configured permissions/allowances.

Acceptance: the owner can inspect why a candidate or strategy was proposed, compare tested versions and follow the linked evidence after reload. Automatic schedules require explicitly saved configuration. Proactive external delivery follows configured notification authorization.

## Deferred work

IBKR execution, Binance spot/futures execution and crypto trading are pending for a later stage. Wider unattended broker execution, automatic strategy promotion/lifecycle management, managed capital actions and production server release retain their separate gates; they do not block the discovery implementation sequence. Preserve ongoing OANDA/Alpaca work and existing account settings.

This plan records direction and acceptance criteria. It does not claim the new discovery capabilities are implemented or verified.
