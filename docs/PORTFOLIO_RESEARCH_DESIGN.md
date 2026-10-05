# Portfolio and strategy research design

Recorded 3 October 2026 from the owner's brainstorming conversation. This records product decisions and working recommendations; it does not certify implementation or enable trading. Continue the existing Tauric platform incrementally. Strategy research belongs to the original scope, rather than being an optional new feature.

## Confirmed product direction

- Priority update, 4 October 2026: the owner deferred IBKR and Binance/crypto trading at this stage and prioritised market intelligence and strategy discovery. These execution routes remain pending in the full scope. Existing crypto data/research can remain available. The immediate research focus is the existing OANDA FX and Alpaca stock/ETF routes; see [current discovery priorities](INTELLIGENCE_DISCOVERY_PLAN.md).
- Scalping is the owner's preferred style, with mixed strategies organised by portfolio.
- Scalping research prioritises higher net returns within the owner's risk limits. Risk limits are eligibility constraints, not weights that higher return can offset.
- Portfolios are organised around a mandate, rather than requiring one asset class each. A portfolio can mix eligible asset classes or explicitly restrict them.
- Both market-context assessment and individual setup review are wanted. Each strategy declares when valid saved context is sufficient and when fresh setup analysis is mandatory.
- Strategy research supports owner-authored rules, configurable templates, Tauric proposals and improvements to existing strategies.
- Fixed and managed capital allocation are both available, selectable independently for each portfolio. Managed allocation operates within configured boundaries.
- Each portfolio has persistent OpenClaw chat with that portfolio's context.
- Portfolio chat can perform actions within its configured permissions, as well as explain results and conduct research.
- OpenClaw helps the owner choose and adjust portfolio objectives, research criteria, numerical limits and other permitted settings conversationally; the owner need not enter every adjustment through Settings.
- Shared account limits apply across all portfolios using the account. Separate portfolio allocations do not imply independent account capacity.
- A portfolio can use one or multiple broker accounts, with explicit account/instrument routing for each strategy deployment and an optional single-account restriction.
- Research supports owner requests, scheduled runs and event-driven triggers per portfolio, including automatic daily research into both potential trading opportunities and new or improved strategies.
- The owner selected automatic strategy promotion as the target workflow, controlled by each portfolio's configured validation gates and permissions for new strategies and strategy updates.
- The owner confirmed automatic pausing, reassessment and retirement of deteriorating strategies under configurable portfolio policies, with explicit handling of existing positions.
- Automatically paused strategies resume automatically after fresh validation under current portfolio/account limits; manual owner pauses remain in force.
- Automation must preserve Tauric as the trading intelligence core. OpenClaw coordinates and governs; it cannot bypass required Tauric assessment, overwrite native decisions or substitute its own positive trading opinion.

Scalping, intraday, swing and experimental research portfolios are illustrative mandates, not mandatory seeded portfolios or presets with invented risk thresholds.

## Responsibilities

Portfolio mandate → strategy definitions → asset-specific scanning → Tauric analysis → OpenClaw governance → deterministic validation → execution → attributed outcomes.

The research engine creates and evaluates strategies. Scanners identify current matches for existing strategy rules. Tauric assesses evidence. OpenClaw coordinates research, resources and portfolio permissions. Platform services own financial validation, execution and authoritative records.

Preserve original Tauric outputs. Governance and reassessments remain separate linked records. Changes to strategy rules create immutable versions; existing deployments retain their pinned versions until a deliberate promotion.

### Tauric intelligence is a required dependency

Automatic strategy promotion requires a valid, preserved Tauric assessment supporting the exact proposed strategy version and its portfolio/market applicability, alongside deterministic research qualification. A backtest score or OpenClaw proposal alone cannot qualify a strategy for automated deployment. The current daily native integration does not yet establish exact-version strategy-assessment capability; implement and verify that integration before claiming this promotion gate is satisfied.

Every automated new entry links the applicable Tauric context/setup assessment, strategy/profile version and evidence. A profile may permit reuse of a still-valid Tauric context or require fresh setup review; both depend on Tauric. Missing, expired, failed, unsupported or adverse required assessment blocks the entry. An analysis budget/deadline failure cannot silently select a path without Tauric. Daily context does not satisfy an unsupported intraday review requirement.

OpenClaw may prioritise, defer, reject or request Tauric reassessment. It cannot convert an adverse Tauric decision into a favourable one, silently change the assessed strategy or overwrite the original reports. A new native assessment is separately linked and retained. Chat settings and automation policies cannot remove the Tauric dependency or waive its evidence/validity checks.

Deterministic scanning, experiment replay, numerical validation, capital/risk enforcement, emergency entry halts and configured protective exits do not require a new AI call for every operation. They preserve the distinction between intelligence and platform execution. Risk/position protection continues under configured rules during Tauric/model outages; it cannot depend on waiting for a new model response. Automatic recovery cannot authorise new entries until their applicable Tauric prerequisites and other current checks pass.

Enforce these references, scope/validity and decision checks in platform promotion/entry services. Prompt instructions are insufficient. Preserve native report identifiers/checksums in qualification, promotion, governance and entry lineage, and test scanner-only, backtest-only, chat-only, stale-context and contradictory-assessment attempts as denied paths.

## Portfolio configuration

Extend the existing portfolio/capital entities rather than introducing a parallel portfolio subsystem. The intended configuration covers:

- Mandate, permitted asset classes and instrument/account eligibility.
- Strategy deployments and exact versions.
- Capital mode, allocations, minimum/maximum boundaries and permitted reallocations.
- Portfolio risk/exposure limits subject to applicable account limits.
- Analysis profiles, context validity, research budgets and model assignments through the shared model registry.
- Research objectives, validation criteria and promotion policy.
- OpenClaw chat/action permissions.

Existing portfolios are account-scoped fixed-USDT allocations. The agreed target adds flexible single-account or multiple-account portfolio routing. Mixed assets do not automatically establish support for other currencies, account products or pooled collateral. Instrument valuation, currency conversion, account routing and reconciliation need explicit supported contracts.

Capital reallocation must account for open positions, pending orders and reserved capital/risk. Record the reason, actor, applicable policy versions and resulting allocations. Reallocation cannot relax owner-configured limits.

### Portfolio account routing

A portfolio groups strategies under a mandate and may span several eligible broker accounts. Every deployment pins its account, instrument mapping, capital allocation and execution mode. An optional portfolio restriction permits only one account. Reuse existing account-scoped allocation and reconciliation records when extending the portfolio model; preserve their historical identity.

Each account retains its own cash, margin, capabilities and limits. Portfolio totals aggregate performance and exposure in a configured reporting currency with recorded valuation sources and timestamps. Missing/stale conversion or marks cannot be silently presented as complete totals or available trading capacity. Aggregated capital is not interchangeable broker buying power.

Planning must satisfy the strategy, portfolio and routed account policies, including shared-account reservations from other portfolios. Route changes create new deployment references and retain the account attribution of existing positions/orders/fills. Existing positions follow their recorded account route and configured exit handling. Portfolio chat can inspect the aggregate and drill down to each account, strategy and original trade lineage.

## Strategy research engine

Idea → explicit strategy definition → historical tests → out-of-sample validation → shadow/paper evaluation → promotion → deployment → ongoing review.

Required capabilities:

- Capture an idea or hypothesis from the owner, a template or Tauric.
- Produce explicit scanner, analysis and execution profiles with declared data requirements.
- Replay the same versioned rules used in deployment, with versioned research/execution assumptions.
- Evaluate costs, data availability and execution behavior appropriate to the market and strategy horizon.
- Separate optimisation data from held-out evaluation; support walk-forward evaluation and comparisons across conditions.
- Preserve unsuccessful experiments, source data, parameters, assumptions and results.
- Recommend portfolio suitability and allocation with inspectable evidence and limitations.
- Monitor deployed outcomes and propose new versions without rewriting historical versions.

Objectives and promotion thresholds are configurable; do not invent a universal score or profitability gate. Scalping research needs explicit spread, latency, slippage, liquidity and fill assumptions supported by the available data. Historical evidence must distinguish qualified point-in-time availability from retrospective observations; native historical AI research remains a separate acceptance gate.

## Research triggers and automatic daily research

The owner confirmed on-request, scheduled and event-driven research, and explicitly requires automatic daily research into potential candidates. This is a platform feature requirement; this note does not create a running automation or initiate model calls.

Each portfolio configures its research universe, enabled research tracks, daily run time/timezone, additional cadence, event triggers, priority and spending limits. Daily automatic research is a supported baseline; other triggers can request additional work under the same applicable portfolio and shared provider/system budgets. Both opportunity and strategy research are confirmed capabilities. The exact daily time, thresholds, universe and budget allocation remain unselected.

Recommended daily workflow:

1. Resolve the portfolio's current mandate, deployments, positions, permitted instruments, data requirements and research policies.
2. Refresh required market/context data and use deterministic scanners or inexpensive checks to shortlist candidates before expensive AI analysis.
3. Reuse compatible fresh research and deduplicate scheduled, event-driven and chat requests. Research validity includes its evidence time, scope and versions; a daily label is not sufficient freshness for a scalp entry.
4. Allocate the available research budget to the most relevant candidates using configured priorities and analysis profiles. Preserve queued/deferred/expired work and reasons.
5. Run Tauric analysis and OpenClaw coordination through the existing separated records. Strategy experiments use the research qualification pipeline; opportunity research uses candidate/context analysis.
6. Save a daily digest in the portfolio chat with source/report links, candidates, findings, uncertainty, proposed actions, costs/usage where measured and job outcomes. An empty shortlist is a valid result.

The owner confirmed both daily research tracks:

- Opportunity research: find potential setups for existing configured strategies, refresh relevant context and produce candidate/watchlist findings with evidence and expiry.
- Strategy research: discover new strategy ideas, improve existing rules and run versioned experiments through the qualification process, producing comparisons and proposals.

Retain distinct records, priorities, budget allocations and outputs for the two tracks within the applicable total research allowance. A strategy idea is not a trade-ready setup. Portfolio chat presents both tracks in the daily digest and lets the owner adjust their scope and research allocation conversationally. Shared context may be reused across compatible tracks and portfolios while preserving each experiment/candidate's attribution.

Define idempotent daily run identity, scheduler timezone/daylight-saving behavior, overlapping-run control, missed-run recovery and data/provider failure handling. Daily work is durable server-side work rather than dependent on the browser remaining open. Retries and re-runs stay within the applicable budgets and retain their attempt history.

Daily research produces findings and proposals. Strategy promotion, capital changes and trading actions follow their separately configured permissions and validation. Chat can change daily scope, schedule, priorities and budget through versioned configuration. Daily digests remain accessible in chat; delivery to external channels follows the owner's configured notification preferences.

## Strategy qualification and promotion

Proposed qualification process for implementation. The owner has agreed to develop this process and selected automatic promotion. Numerical thresholds, objective definitions/weights, permitted target modes and exact portfolio permissions still require configuration. These are design requirements, not implemented gates or evidence that a strategy qualifies.

Qualification belongs to an exact strategy version, portfolio policy version, instrument/account route and proposed execution mode. A strategy can qualify for one portfolio or mode without qualifying for another.

Use separate eligibility checks and portfolio-specific research criteria. Integrity, permission and supported-capability checks must pass. Portfolio objectives rank otherwise eligible strategies; an attractive performance score cannot override a failed eligibility check. Missing evidence is incomplete, not passed. Exploratory research and experimental paper evaluation remain available with explicit status and configured permissions, without implying live qualification.

### 1. Freeze the research contract

Before evaluation, record the hypothesis, exact strategy version, scanner/analysis/execution requirements, portfolio objective, evaluation protocol, data splits, parameter search space, cost assumptions and acceptance criteria. Preserve the complete experiment family, including rejected variations and prior inspections of evaluation data.

Changed rules create a new strategy version. Changed evaluation criteria create a new policy/protocol revision and qualification attempt; do not retrospectively rewrite a failed result as a pass.

### 2. Verify rules and evidence

Replay the same registered rules used by deployment. Validate reproducibility, instrument/session mappings, price/volume semantics, data gaps, timestamps and required features. Freeze dataset, algorithm and configuration references with checksums. Signals cannot consume evidence unavailable at their decision time.

Retrospective candles may support appropriately labelled deterministic experiments. Historical AI evidence needs its own qualification, including retrieval availability and possible model knowledge of later events. A receipt timestamp attached to an old candle does not establish historical availability.

### 3. Evaluate performance after costs

Use the proposed route's declared fee, spread, slippage, sizing and execution model. Record funding/financing and currency conversion where relevant. Scalp evaluation must account for analysis/order delay, fill availability and liquidity; candle-only results cannot certify assumptions requiring quotes, ticks or order-book evidence.

Report net outcomes, marked equity drawdown where data supports it, exposure, holding time, turnover, loss streaks and sample uncertainty. Keep realized and marked metrics distinct. Trade count and calendar coverage are separate criteria; correlated trades are not treated as independent observations merely because the count is large. No universal minimum trade count, win-rate target or return threshold is selected here.

### 4. Test robustness and selection effects

Evaluate frozen candidates on chronological unseen data and across successive train/evaluate windows where appropriate. Account for overlapping observations and strategy lookbacks/holding periods in split design. Track every tested variation and apply a suitable selection-bias/overfitting assessment when searching many alternatives. Repeated inspection or tuning against evaluation data ends its untouched status.

Stress configured costs, delays, missed fills and nearby parameter values. Report performance by session, instrument and relevant market conditions, including conditions where the strategy should remain inactive. Freeze the final candidate before forward observation.

Methodological reference: Bailey, Borwein, López de Prado and Zhu, [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). The paper motivates accounting for the strategy-selection process; its methods do not supply universal portfolio acceptance thresholds.

### 5. Validate forward behavior

Shadow mode observes live opportunities and records hypothetical decisions without submitting orders. Paper mode exercises the configured simulated order/fill/accounting path. Each stage has configurable duration, evidence coverage and criteria appropriate to the strategy and target mode.

Compare predicted and observed candidate frequency, analysis deadline misses, spread/cost assumptions, fill behavior, errors and attribution. A simulated fill does not establish actual broker fill quality. Broker demo/test paths and supported-capability verification are separate prerequisites for their applicable execution routes.

### 6. Check portfolio fit and promote explicitly

Assess proposed capital, overlapping exposures and observed outcome relationships with deployed strategies using comparable data where available. Enforce portfolio and account limits, reservations, supported instruments and current reconciliation. Insufficient portfolio-fit evidence remains visible.

Produce an immutable qualification report containing the requested portfolio/mode/route, pinned strategy/policy/protocol versions, source experiments, costs, metrics, uncertainty, passed/failed/incomplete criteria, validity conditions and reasons. Suggested qualification states: incomplete, failed, eligible and superseded. Promotion is a separate record from eligibility.

Automatic promotion is the owner's chosen target workflow. A portfolio's versioned policy defines whether qualifying new strategies and strategy updates may be promoted automatically, the eligible account routes and target execution modes, validation gates, capital limits and promotion cadence. Manual review remains available for portfolios or action categories outside automatic authority. Present the exact version, destination, mode, allocation, changes and supporting evidence in the promotion record and portfolio chat.

Automatic promotion rechecks current qualification validity, portfolio/account policies, reserved capital/risk, route capabilities and deployment state at admission. Store an idempotent promotion identity and persist the new deployment/version references atomically with required reservations. Conflicting or stale evidence/policy cannot be treated as successful promotion. Record skipped/blocked promotions with reasons and links. It remains a platform-controlled action, not a prompt-only decision.

Promotion applies the new version to future entries. Existing positions and their pending exit/protective-order lineage retain their recorded opening version and route, unless a separately authorised transition explicitly defines the handling. Version switches must account for overlapping positions/reservations and cannot reset signal-consumption history to duplicate an entry. Successful promotions notify the portfolio chat with their evidence and resulting references.

Research qualification is separate from platform/broker release readiness and permission to submit real orders. Selecting automatic promotion in this design does not enable live accounts, change deployed rules or approve any concrete trade. Automatic mode transitions follow explicit portfolio/account policies and route readiness; promotion cannot silently broaden its permitted mode or capital limits.

### 7. Monitor after deployment

Record actual results, costs, data health and analysis/execution deadline behavior against the qualified assumptions. Versioned portfolio policy defines reassessment triggers and entry-pause conditions. Entry suspension follows the configured handling of existing positions and protective orders. Reassessment creates new records; it does not erase prior qualification or change deployed rules silently.

A changed strategy, route, material data/execution assumption or applicable policy requires the relevant checks to run again. Expiry and invalidation criteria are explicit. Operational readiness is rechecked at action time, even when the research report remains valid.

### Automatic pause, reassessment and retirement

The owner confirmed automatic lifecycle management. Each portfolio configures trigger definitions, measurement windows, required evidence, action permissions and escalation/retirement criteria. Numerical thresholds remain unselected. This is intended product behavior; no lifecycle automation is enabled by this document.

Distinguish operational blocks from performance deterioration. Data/provider failures, reconciliation discrepancies, account halts and unsupported capabilities block entry through deterministic platform validation. Performance triggers compare saved costs, outcomes and exposure with the portfolio's versioned criteria and the strategy's qualified assumptions. A missing metric is not fabricated evidence of good or poor performance. Provider downtime alone is not evidence that a strategy should be retired.

Lifecycle actions:

- Pause new entries when a configured operational or performance boundary is reached. Mark the action cause, source evidence, effective policy and affected route/version.
- Reassess the exact version and investigate whether the cause is data/execution quality, market applicability or rule performance. Queue research under its budget and preserve existing reports. Operational blocking does not wait for an AI review.
- Continue observation or propose a new version when evidence supports it. Revised rules pass the full applicable qualification and promotion process.
- Retire the deployment when its configured criteria are met. Preserve its strategy/version, positions, fills, qualification and lifecycle history. Retirement ends new entries; it does not delete records.

Lifecycle control is separate from reassessment job state. Maintain distinct active, entry-paused, retiring and retired deployment states with linked research jobs and reasons. Retiring can retain unresolved positions/orders; operational records remain live until the configured exit/cancellation handling is complete and reconciled.

Pausing/retirement follows the portfolio/account policy for pending entry orders, existing positions and protective exits. Do not release reserved capital merely because entry is paused or retirement requested. Release follows confirmed cancellation/closure and reconciliation. Existing positions preserve their opening strategy/version/account attribution and configured management. Any forced-close behavior requires explicit policy authority and the normal execution checks; performance deterioration does not silently replace exit rules.

Use version-aware, idempotent actions with recorded before/after state, reason, timestamp, source evidence and applicable permissions. Concurrent promotion/reallocation cannot reopen a paused route or spend its reservations. Recheck lifecycle state during entry admission and submission. Chat shows pause, reassessment and retirement receipts, explanation, retained positions and any required owner action; the daily digest links to these events.

The owner selected automatic resumption after fresh validation for automatically paused strategies. Resume must satisfy current data health, reconciliation, portfolio/account limits and the relevant qualification requirements, and record the validating evidence and policy revisions. All blocking causes must be resolved; passing a performance check does not clear a separate account halt or unhealthy route. Recovery cannot silently reset lifetime performance, reservations or signal-consumption history. Track manual pauses/account halts separately so automatic recovery cannot undo an explicit owner stop.

Retirement does not trigger immediate reactivation merely because a later metric improves. Reactivating a retired strategy is a new qualified promotion governed by its portfolio policy. Trigger persistence and pause/resume cooldowns are configurable to control repeated transitions; no universal trade-count or cooldown value is selected here.

### Portfolio objectives and criteria

Each portfolio configures its research objective and acceptance criteria independently. Candidate objective profiles include balanced net return/drawdown/consistency, drawdown-and-consistency priority, and net-return priority within explicit risk limits. The owner selected net-return priority within risk limits for scalping research. Other portfolios can choose different objectives.

For this scalping objective, apply evidence, robustness, capability and risk eligibility checks first, then rank qualifying strategies by the declared after-cost return metric over comparable evaluation windows and capital assumptions. Record whether the objective uses percentage or absolute return, the capital denominator, valuation method and evaluation horizon in the research contract. Higher return cannot compensate for a failed risk gate, missing evidence or unsupported execution assumptions. Numerical limits and the precise return definition still require configuration.

Configurable criteria include drawdown definition/cap, net-outcome requirements, evidence duration and sample adequacy, robustness/cost tolerance, forward-observation requirements, exposure limits and report validity. Do not silently supply financial thresholds. Objective selection alone does not enable a deployment or approve a capital allocation.

## Context and setup analysis

Market context is reusable evidence with an instrument/market scope, strategy applicability, source/configuration versions, expiry and invalidation conditions. A setup review references the exact candidate, strategy version, timeframe and fresh evidence.

Each strategy declares its analysis requirements and deadline. Late or expired analysis cannot authorise entry. Protective orders and configured exit handling operate through execution rules independently of AI response time.

The current native path performs daily research, while scanner evidence may be intraday. Scanner features are currently linked metadata rather than consumed native inputs. Resolve this contract explicitly; do not label daily research as completed intraday setup analysis. Preserve the USD native research versus USDT market-data distinction.

## Portfolio OpenClaw chat

Portfolio chat should expose its selected portfolio visibly and load its mandate, policies, strategies, positions, orders, performance, research and pending proposals. General chat remains available for account-wide questions and portfolio comparisons.

Action permissions are granular:

- Research requests and strategy comparisons.
- Strategy/version proposals and permitted promotion, pause or resume actions.
- Capital proposals and permitted managed reallocations.
- Trading requests through the existing governance, risk and execution services.

Authoritative portfolio context and permissions must be enforced by platform services, rather than relying on prompt text. Chat cannot grant itself permissions or change the target portfolio implicitly. Retain conversation/message references, effective policy, reason, action results and resulting resource references. Shared market research may be reused where evidence, versions and freshness are compatible; portfolio decisions remain attributed separately.

### Conversational adjustments

Portfolio chat is an editing surface for the same versioned configuration used by Settings. OpenClaw can explain the current values, use saved research and performance evidence to recommend changes, compare alternatives, and apply owner-directed changes within the portfolio's granted authority.

Supported adjustment categories include research objectives and ranking criteria, validation thresholds, analysis/research budgets and cadence, permitted instruments, portfolio risk limits, capital-allocation mode and boundaries, and strategy parameters. Strategy parameter edits create a new strategy version; changing research or portfolio criteria creates a policy revision. Configuration edits alone do not promote a strategy or grant execution authority.

For each adjustment, identify the portfolio and exact field, units/basis, current value, proposed value and operational effect. Ask a focused question when the intent is ambiguous. For example, “make this more aggressive” needs a concrete interpretation, and “maximum drawdown” needs a stated equity basis and measurement window. Use recorded evidence for recommendations and identify unavailable evidence rather than inventing suitable thresholds.

Clear instructions covered by existing permissions can be applied directly with a visible change receipt. When a portfolio policy requires review, show the exact proposed diff and obtain that review. Do not add a universal confirmation step for every conversational edit. Owner-directed limit revisions and OpenClaw's own managed allocation actions have distinct authority: automatic management remains within the owner's effective limits, and broader account constraints continue to apply.

Use expected-revision checks, validated typed fields and idempotent requests through platform services. Record the requesting message, actor, reason, before/after values, resulting revision and action status. Report a change as applied only after the authoritative service confirms it. History/restore use the same configuration records as Settings; restoring creates a new revision.

Explain whether an edit affects future research only, requires reassessment of existing qualification, or changes active operational policy. Show affected deployments and the configured handling of pending orders/open positions. Previously qualified results retain their original policy references; they cannot be relabelled as passing newly edited criteria without the relevant checks.

## Working recommendations and open choices

The owner chose automatic promotion as the intended product behavior. Per-portfolio boundaries, separate permissions for new strategies/updates, numerical gates and eligible execution modes remain to be configured. The design decision does not itself activate a deployment or an execution account.

Further choices needed before dependent implementation:

- First market/data route for the complete scalping research workflow.
- Research objectives and validation/promotion criteria for each mandate.
- Daily research run time/timezone, universe, budgets and priorities; automatic promotion boundaries, target modes and gates.
- Lifecycle deterioration/retirement criteria, recovery validation and transition cooldowns.
- Reporting currency and valuation requirements for portfolios spanning accounts/currencies.
- Conversation organisation within a portfolio; one or several named conversations.

## Existing foundation and remaining work

Preserve immutable strategy/deployment records, fixed capital allocations, attributed paper accounting, manual crypto scanner records, research admission/deduplication, saved chat and the independently deployed OpenClaw runtime.

The documented research foundation provides retrospective spot datasets, SMA replay and paper analytics. Newer source also contains pinned breakout replay using shared rule evaluation and dedicated tests; review its latest verification status before treating registered-strategy replay as absent or complete. It does not yet establish the full versioned-strategy research lifecycle. Portfolio-bound actionable chat, managed reallocation, mixed-market support, strategy-aware setup analysis and the complete research/promotion lifecycle require implementation and verification. The [portfolio implementation plan](PORTFOLIO_IMPLEMENTATION_PLAN.md) maps these requirements to the current code and incremental acceptance gates, including developing paper-autonomy structures.

Scheduled paper rule monitoring now exists; it evaluates signals without creating proposals, reviews or fills. Scanner scheduling and autonomous execution are separate capabilities. Check current source and verification documents before assigning status.

Related documents: [Requirements coverage](REQUIREMENTS.md), [Research and analytics](RESEARCH_ANALYTICS.md), [Scanner verification](SCANNER_VERIFICATION.md), [OpenClaw chat](OPENCLAW_CHAT.md), [Strategy scheduling verification](STRATEGY_SCHEDULING_VERIFICATION.md), and [Server readiness](SERVER_READINESS.md).

Implementation update (3 October 2026): the bounded shared daily research coordinator is implemented; see [verified behavior](PORTFOLIO_DAILY_RESEARCH_VERIFICATION.md). The owner selected FX and stocks together as the next working data/research adapters after crypto. This is priority, not current adapter readiness. Daily market context is not exact-version intraday qualification.

Subsequent implementation: [read-only FX/stock adapters](MARKET_DATA_ADAPTERS.md) and [exact-version crypto research-comparison qualification](STRATEGY_QUALIFICATION.md) are implemented. The separate native strategy assessment extension cannot be replaced by daily BUY/HOLD/SELL output. Live scalping qualification and automatic promotion still need their applicable forward execution and platform evidence.
