# Portfolio implementation plan

Prepared 3 October 2026 from the [agreed portfolio and research design](PORTFOLIO_RESEARCH_DESIGN.md) and inspection of the current source. This is the implementation sequence for the portfolio/research requirements within the existing release scope, not a replacement architecture or a reduced release. Preserve the broader [build plan](BUILD_PLAN.md) and [server release gates](SERVER_READINESS.md).

## Current priority — 4 October 2026

The owner prioritised market intelligence and strategy discovery and deferred IBKR and Binance/crypto trading at this stage. They remain pending; their integration is not a prerequisite for the current discovery work. Existing data/research and OANDA/Alpaca workflows are preserved. [Intelligence and discovery priorities](INTELLIGENCE_DISCOVERY_PLAN.md) supersede the older recommended-first-slice ordering below. This direction does not enable research schedules, trading or paid model calls.

The first research-universe stage is implemented and deployed locally: searchable OANDA/Alpaca catalogs, provider-verified additive instruments and shared scanner/portfolio selections. See [discovery verification](INSTRUMENT_DISCOVERY_VERIFICATION.md) for current coverage and universe bounds. Market intelligence, opportunity ranking and generated strategy hypotheses are the next stages.

The first bounded portfolio/chat slice is implemented and verified: see [portfolio chat verification](PORTFOLIO_CHAT_VERIFICATION.md) for actual capabilities and remaining work. The table below records the planning baseline; the newer verification document supersedes its chat and autonomy findings.

## Current implementation and evidence

| Capability | Current finding | Treatment |
|---|---|---|
| Workstation, identity, configuration history and audit | Existing implementation and verification documents | Preserve and extend |
| Capital portfolios and nested pots | `capital.py`, `capital_contracts.py`, `CapitalScopeVersion`; fixed USDT and account-scoped | Extend with portfolio mandates and account-allocation links; preserve financial history |
| Strategy versions and deployments | Existing immutable versions and explicit account/instrument/allocation routes | Reuse as the deployment authority |
| Paper execution and virtual attribution | Existing reviewed entry/close, Decimal sizing, shared limits and reconciliation | Reuse as the first execution route |
| Scheduled strategy evaluation | Saved schedules, independent monitor and recorded verification | Preserve; this is not scanner/research scheduling or autonomous dispatch |
| Crypto scanner | Manual closed-bar screening, candidate/match evidence, expiry, deduplication and bounded research admission | Extend the same subsystem |
| Native Tauric and OpenClaw governance | Separate original artifacts, queues and linked governance; current daily crypto context | Preserve separation and explicitly extend analysis context |
| Chat | Saved conversations and bounded daily native research dispatch; requests/conversations lack portfolio identity | Add authoritative portfolio binding and scoped tools |
| Research | Retrospective datasets and costed replay; source now includes pinned breakout replay using shared `evaluate_closed_bars` and dedicated tests | Review/verify the newer breakout work; do not recreate it |
| Autonomous paper execution | Source contains policy/workflow models, contracts and migration `0015_autonomous_paper`; no verified complete dispatch path established by this review | Treat as developing work and reconcile its latest status before editing |
| Models | Fixed cloud/local profiles with quick/deep assignments and encrypted revisions | Incrementally migrate to the agreed provider/model registry |
| Analytics | Closed-paper-trade projections; full marked equity and live performance remain gaps | Preserve definitions; add qualified marks before equity-based lifecycle limits |
| Broker adapters and deployment release | Full coverage and server gates remain pending in the release documents | Keep full scope; qualify each route and release separately |

This review inspected source and existing verification records. It did not rerun runtime, browser, financial or deployment tests. Developing files can advance independently; verify their current status before implementation and use recorded evidence rather than assuming that a model, migration or test file establishes a completed feature.

## Implementation principles

- Extend the current API, database, frontend, workers and independently deployed OpenClaw integration. Avoid parallel portfolio, scanner, research or accounting subsystems.
- Keep portfolio business identity separate from the money held at a particular account allocation, while preserving existing entity references. Plan an additive migration; do not reinterpret historical fills or move money to create the new identity.
- Every write uses typed validation, owner/portfolio context, applicable permissions, expected revisions, idempotency and linked receipts. Financial writes retain existing locks/reservation/reconciliation requirements.
- Define a complete owner-visible workflow and acceptance evidence for each stage. Do not mark the full capability done when only configuration, a tab or a contract exists.
- Keep paper/shadow/live capability and qualification statuses distinct. Daily research is not automatically valid intraday/scalping setup analysis.
- Preserve Tauric as the required trading intelligence dependency: automated promotion links an exact-version strategy assessment plus qualification, and every new entry links valid applicable Tauric context/setup evidence. OpenClaw coordinates, can reject or request reassessment, and cannot overwrite or reverse a native decision. Deterministic screening/replay/risk/protective actions do not substitute for these intelligence prerequisites.
- Store objectives, schedules, budgets, permissions and thresholds as configuration accessible through Settings and permitted portfolio chat actions. Do not invent trading limits for the owner.

## Stage 0 — reconcile developing work and migration baseline

Before financial/schema edits, identify the latest completed migration, current replay/autonomy work and any accompanying verified evidence. Preserve that work and coordinate changes through current file state. Establish a restorable pre-migration backup and validate the additive migration on a disposable database.

GitHub/source readiness can progress alongside feature work: this folder currently appears untracked under a parent Git repository. Establish the intended existing repository/remote, tracked source and exclusions before release work; do not create a replacement repository or commit credentials/artifacts automatically.

Acceptance: an explicit baseline identifies reusable modules, developing work and the applicable migration head. A migration plan preserves existing owners, allocations, deployments, conversations and financial records.

## Stage 1 — portfolio workspace, policies and account links

Create the shared portfolio context used by chat, research and automation. Extend existing portfolio data with a stable mandate identity, immutable policy revisions and explicit links to account-scoped allocations. Legacy account portfolios retain their account/allocation references and histories.

Add a portfolio selector/workspace with mandate, strategy deployments, account routes, research status, capital and performance views. Support one or multiple account links without representing separate account balances as pooled buying power. Begin aggregation with supported USDT paper accounts and clearly defined realized metrics. Other currencies/products need verified valuation and capability support before comparable totals are shown.

Store the agreed fixed/managed capital choice, objectives and action permissions, exposing capability status honestly until the corresponding action service exists. Existing account limits remain authoritative. No active portfolio policies or numerical risk thresholds are seeded merely to demonstrate the UI.

Acceptance:

- Two portfolios and their account/strategy links persist across restart; existing allocations/fills remain unchanged.
- Switching portfolios changes all bound views and URLs consistently. Owner/resource validation rejects inappropriate references.
- Policy changes preserve history and reject concurrent stale writes.
- Unsupported totals/managed actions explain their actual capability state; account-level capacity is never inferred from an aggregate portfolio total.

## Stage 2 — portfolio chat with real configuration actions

Bind persistent conversations and their tool/session context to the portfolio on the server. Keep existing general conversations as general; do not assign an invented portfolio to historical messages. Use one main conversation per portfolio with optional additional conversations as a routine interface default; all share authoritative portfolio context. Preserve general chat for account-wide comparisons.

Read tools retrieve mandate, deployments, positions, reports, settings and defined performance. First write tools change mandate/research preferences and pause/resume existing paper deployments under applicable permissions. Add financial and promotion tools only as their stage-specific services become ready. Manual pauses remain distinct from automatic pauses.

Chat and Settings call the same versioned services. A clear permitted instruction can apply directly and returns a change receipt; ambiguous requests ask for the exact value/basis. Review-required policies present a concrete diff. Model text alone cannot certify that a change succeeded.

Relevant modules: `chat_contracts.py`, `chat_api.py`, `chat.py`, `chat_worker.py`, OpenClaw project tools and `frontend/src/chat/Chat.tsx`, using existing strategy/capital/configuration services.

Acceptance:

- Two portfolio chats retain separate histories and authoritative context; general chat continues to work.
- Prompt/session/request attempts to switch the target portfolio cannot redirect a write silently.
- A permitted change appears in Settings and audit history; denied, ambiguous and stale requests do not create a false success receipt.
- Repeated tool calls do not repeat the change. Pausing entries preserves existing position management and manual-stop state.

## Stage 3 — daily and event-driven research, both tracks

Add a durable portfolio research coordinator using the existing worker/queue patterns. Support owner chat requests, automatic daily runs, additional schedules and versioned event triggers. Store timezone, local run time, scope, track priorities and budgets. Europe/London is the scheduling preference default; the owner selects the actual daily time. Handle daylight-saving transitions and missed/overlapping runs explicitly.

Both tracks are required:

- Opportunities: deterministic scanning → ranked shortlist → fresh compatible context/research → candidate/watchlist findings.
- Strategies: hypotheses/templates/typed Tauric proposals → recorded experiments and comparisons → qualification requests as Stage 4 becomes available.

An initial limited strategy track may generate and replay proposals for supported rule families, but it is not the complete qualification/promotion engine. Preserve rejected ideas and experiment families. Typed strategy proposals reference registered detector/rule engines; they are not arbitrary model-generated code execution.

Extend candidate/research deduplication and caching with evidence, model/configuration and context validity. Add shared admission/reservation accounting across chat, schedules, scanner and governance; retain per-portfolio/track attribution when research is reused. Incrementally migrate cloud/local profiles into provider/model connections and task routing with compatibility aliases. Capture provider usage and costs where measurable; unknown usage stays unknown. Admission-count limits must not be presented as monetary guarantees.

Post an idempotent saved daily digest in portfolio chat with both tracks, report links, deferred work, failures and measured usage. Notification delivery uses configured channels/recipients; daily digest storage is independent of outbound delivery.

Acceptance:

- With the browser closed, one due daily run records both tracks and a digest. An empty opportunity shortlist and an incomplete strategy experiment are represented accurately.
- Chat/event/scheduled duplicates reuse compatible work without duplicate admissions or duplicated financial authority.
- Budget exhaustion, provider failure, stale candidates, service restart and timezone changes have recorded deterministic outcomes.
- Broad-universe screening makes no LLM calls; expensive work is bounded and prioritised.

## Stage 4 — registered-strategy experiments and qualification

Implementation update: the bounded exact-version crypto breakout research-comparison qualification path is implemented, with frozen protocols, separate holdout/stress evidence, registered exposure checks and a separate structured native strategy review. See [strategy qualification](STRATEGY_QUALIFICATION.md). Forward/shadow evidence, latency stress, additional families, daily automatic qualification, paper/live promotion and lifecycle integration remain open; a passing research snapshot does not authorize trading.

Finish and verify the existing shared-rule breakout replay first. Preserve its execution assumptions: sharing signal logic does not make next-open OHLC fills equivalent to live execution. Extend the registry to other selected strategy families incrementally.

Add experiment protocols, pinned strategy/policy/data versions, train/evaluate windows, parameter search history, cost/latency stress and marked research metrics. Record repeated inspections and optimisation trials. Keep retrospective deterministic evidence distinct from qualified historical AI/news evidence.

Add forward shadow/paper observation and immutable qualification reports whose scope includes portfolio, strategy version, route and intended mode. Require explicit supported metrics, sample/coverage criteria and costs. The scalping objective ranks eligible candidates by configured after-cost return within risk limits; other portfolio objectives remain configurable.

Integrate and verify Tauric assessment of the exact strategy version and applicability as a distinct required qualification input, preserving native evidence. Current daily instrument research cannot be relabelled as this capability. An unavailable assessment leaves automated-promotion eligibility incomplete.

Acceptance:

- A saved executable strategy can be replayed and independently reconstructed using its pinned rules/data/cost assumptions.
- Future-data leakage, changed-version references and relabelled failed criteria are rejected; missing evidence remains incomplete.
- Unseen evaluation and stress results remain separate from optimisation results, including discarded variants.
- A report identifies each passed/failed/incomplete criterion and cannot qualify unsupported scalping fill assumptions or target modes.
- Backtest success without the required exact-version Tauric assessment cannot qualify automated promotion; original Tauric artifacts remain immutable and linked.

## Stage 5 — automated promotion, capital and lifecycle management

Implement promotion as a platform service linking current qualification to a new immutable deployment. Reuse developing paper-autonomy work after its current verification is established; distinguish entry/exit dispatch from strategy qualification/promotion. Add managed allocation actions using existing account locks, reserved capital/risk and explicit route capabilities.

Implement pause → budgeted reassessment → validated automatic resume or retirement. Add qualified position marks and portfolio/account exposure measurements before activating lifecycle rules that depend on total equity or unrealized drawdown. Operational data/reconciliation blocks remain deterministic and independent of model response time.

Existing positions preserve their original version/route/exit lineage. Pending orders and reservations follow explicit cancellation/closure rules. Manual owner pauses and account halts are not cleared by automatic recovery. Retired records persist; subsequent activation is a newly qualified promotion.

Complete the first automated closed loop on supported paper accounts/rule families and explicitly permitted analysis requirements. A strategy requiring unimplemented intraday Tauric review remains blocked until Stage 6 provides it.

Acceptance:

- Portfolio research → experiment → qualification → automatic promotion → paper dispatch → attributed outcomes → deterioration/reassessment/resume is demonstrated with all records inspectable.
- Competing promotion, allocation and entry requests cannot exceed applicable capacity or duplicate an entry.
- Interrupted/retried workflows preserve committed actions and resolve unknown states before repeating financial writes.
- Invalid/stale qualification blocks promotion; pauses/retirement do not prematurely release reservations; manual stops survive recovery.
- Promotion and entry reject missing/adverse/stale/unsupported Tauric assessments, including scanner-only, backtest-only, chat-only and budget-fallback attempts. Valid cached Tauric context is accepted only within its declared scope/expiry and strategy analysis profile; mandatory fresh setup review cannot be skipped.
- New versions affect eligible future entries and retain existing-position history/management.

## Stage 6 — strategy-aware scalp analysis and remaining markets/routes

Extend native analysis with explicit strategy/timeframe/as-of evidence and configurable analysis profiles. Validate reused context versus mandatory fresh setup review, analysis deadlines and invalidation. Preserve original Tauric artifacts and USD/USDT/provider distinctions. A daily report cannot stand in for unavailable scalp setup analysis.

Add a shared adaptive quote/tick/depth subscription manager and venue-aware derived features. Demonstrate the declared execution/data assumptions for the first actual scalping route, including delay, spread, sizing, missing data and fill behavior. Use the currently supported crypto path for initial integration mechanics, not as a decision about the owner's eventual preferred live market.

Extend Forex, Stocks, Small Caps and Event/Macro families using the common candidate/profile/admission infrastructure and asset-specific detectors. Add catalysts/rumours and shared macro/theme research caching, chart setup overlays/intelligence and richer analytics as working integrations rather than placeholder tabs. Preserve the agreed broker and product scope in the main build plan; qualify account capabilities and adapter behavior separately.

The owner only needs to choose the first specific broker/market/data route when its dependent connected work begins. No such choice blocks Stages 1–4.

Acceptance: the selected scalp workflow consumes qualified data and context, handles stale/deadline failures and completes the supported paper/demo route with realistic documented assumptions. Each additional scanner/broker/product has its own coverage/evidence; incomplete routes remain visibly unavailable.

## Stage 7 — release and server readiness

Complete versioned release images/manifests, intended GitHub source tracking, production Compose/preflight, private secrets/ingress, persistent data, off-host backups, restore/upgrade/rollback rehearsal and measured server load/recovery gates from the existing release plan. Test research/automation load and bounded queues together, including failures and restarts. Retain independent Tauric/OpenClaw upgrades and compatibility checks.

Acceptance: existing [server readiness criteria](SERVER_READINESS.md) pass for the supported host/release. Connected account/broker verification remains explicitly recorded per route. The full release still includes the remaining original workstation/intelligence/chart/risk/broker capabilities; this plan does not silently remove them.

## Requirements-to-stage checklist

| Agreed requirement | Primary stage |
|---|---|
| Mandate-based mixed portfolios and multiple account links | 1, with verified valuation/product coverage in 6 |
| Independent objectives, capital modes, permissions and policy history | 1; working managed financial actions in 5 |
| Persistent portfolio OpenClaw chat and conversational edits | 2; financial/research action extensions in 3–5 |
| Automatic daily research on opportunities and strategies | 3; full experiments/qualification in 4 |
| Shared prioritisation, cache and cross-channel research budgets | 3 |
| Unified provider/model registry and usage | 3, with verified task/route coverage as it expands |
| Exact-version strategy replay and robust qualification | 4 |
| Return-priority scalping research within configured risk limits | 4; qualified scalp data/execution assumptions in 6 |
| Automatic promotion, separate new/update permissions | 5 |
| Automatic pause/reassess/resume/retire with manual stops preserved | 5 |
| Market context plus individual setup analysis | 3 for current daily scope; 6 for strategy-aware scalp scope |
| Asset-specific scanners and adaptive market data | 3 for current crypto scheduling; 6 for additional capabilities |
| Full broker/live and server release | 6–7 plus the original release gates |

## Recommended first implementation slice

Stages 1–2 form the next bounded owner-visible slice: select an existing paper portfolio, save its mandate/research preferences, open persistent scoped chat, inspect only the bound portfolio context, apply a permitted setting change and pause an existing paper deployment with a linked receipt. Demonstrate it for two portfolios, with persistence and negative scope/permission/conflict checks.

This establishes the configuration and context required by daily research and automation. It introduces no second trading engine and does not require a broker credential, arbitrary financial threshold, paid model test or live order to implement/verify the mechanics. Real connected chat verification is a separate recorded check using the configured route.

Further questions are deferred until they block connected work or actual portfolio configuration. Routine interface choices follow the defaults above; owner-facing numerical trading/research settings remain explicit rather than being guessed.

## Stage 3 bounded implementation update — 3 October 2026

The daily coordinator, versioned schedules/event allowlist, manual/scoped chat runs, existing scanner shortlists, owner-defined costed SMA/saved-breakout experiments, bounded neighbour exploration, compatible evidence reuse, shared native/governance admission-count cap, durable digests and same-service UI/chat controls are implemented. See [verification and limitations](PORTFOLIO_DAILY_RESEARCH_VERIFICATION.md). No owner policy is automatically enabled.

FX and stocks/ETFs now have read-only chart/data and explicit-cost SMA replay adapters, with encrypted credentials entered in Settings → Market data. Real feed connectivity, native assessment and broker execution remain separate gates. Remaining Stage 3 work includes automated hypothesis/proposal generation beyond bounded owner plans, provider usage/cost capture and proactive digest delivery. The separate bounded Stage 4 research-comparison qualification path is implemented; this coordinator does not automatically qualify experiments or complete subsequent promotion/recovery/capital-management stages.
