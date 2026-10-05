# Build and validation plan

The screenshot-inspired portfolio and research interface additions are scheduled in the [post-launch to-do list](POST_LAUNCH_TODO.md), as requested by the owner on 5 October 2026.

**Current owner priority, 4 October 2026:** market intelligence and strategy discovery. IBKR and Binance/crypto trading are deferred at this stage and remain pending in the full scope. Continue discovery on the existing FX and stock/ETF foundation; see [current work order and acceptance criteria](INTELLIGENCE_DISCOVERY_PLAN.md). Broker expansion and full unattended execution do not block this research work.

OANDA/Alpaca instrument discovery, provider search and additive selection now extend the shared registry beyond the starter catalog. Scanner/portfolio research subsets reuse their existing versioned settings; market-data and native-research eligibility remain explicit. See [discovery verification](INSTRUMENT_DISCOVERY_VERIFICATION.md). General market intelligence and automated strategy-hypothesis generation remain pending.

The owner's portfolio/research decisions are recorded in [Portfolio design](PORTFOLIO_RESEARCH_DESIGN.md). [Portfolio implementation plan](PORTFOLIO_IMPLEMENTATION_PLAN.md) maps them to the current source and defines the next incremental slices, including scoped chat, daily opportunity/strategy research, qualification and automatic promotion/lifecycle management. It supplements this full-release plan; developing replay/autonomy work must be reconciled before implementation.

Executable strategy rules now have an initial on-demand paper implementation: a pinned closed-candle breakout family, single-use reviewed entry signals, evidence validation and attributed stop/target/channel exit actions. Opt-in schedules now evaluate current closed candles and monitor attributed exit rules in the background, with atomic recovery and immutable check receipts. Autonomous dispatch and broader families remain pending; see [scheduled monitoring](STRATEGY_SCHEDULING_VERIFICATION.md). See [executable strategies](EXECUTABLE_STRATEGIES.md).

The agreed full release remains the target. Milestone 1 is implemented locally; see [foundation verification](FOUNDATION_VERIFICATION.md). Milestone 2 has real public spot charts, immutable snapshots, a pinned native worker and a successful connected daily BTC analysis with all nine original report fields. FX/intraday/futures and point-in-time acceptance gates remain open; see [connected AI verification](CONNECTED_AI_VERIFICATION.md). Milestone 3 has owner-reviewed paper execution, shared portfolios/nested pots, immutable strategy versions, explicit paper account/allocation routes and pause controls. Separate queued OpenClaw governance and real no-tool model transport work; one real bound local tool rejection review now passes; a fresh real approval-to-paper-entry/close workflow now passes independent journal reconstruction and API-restart persistence in an isolated daily BTC environment. See [connected paper verification](REAL_PAPER_WORKFLOW_VERIFICATION.md). See [strategy verification](STRATEGY_VERIFICATION.md), [capital verification](CAPITAL_VERIFICATION.md), [paper verification](PAPER_VERIFICATION.md) and [governance verification](GOVERNANCE_VERIFICATION.md). Milestone 4 now has its first manual crypto spot scanner slice: versioned profiles, immutable candidates, deduplication, expiry and bounded daily research escalation. See [scanner verification](SCANNER_VERIFICATION.md) and [real governance verification](REAL_GOVERNANCE_VERIFICATION.md). Full routing, automatic scanner scheduling, the other intelligence/research features, autonomous execution and milestones 5–6 remain pending.

Development 0.9.0 adds bounded native daily research requests to saved OpenClaw web conversations and native owner-only Telegram/Discord DMs. Owner/session-bound admission, shared daily allowances, cross-channel reuse, progress/failure cards and original report links are implemented. Real web transport and research tool admission are verified; external delivery needs owner bot credentials. Orders, governance permissions and configuration dispatch remain outside chat. See [OpenClaw chat](OPENCLAW_CHAT.md).

Research and analytics now include immutable retrospective spot datasets, a costed closed-candle SMA/next-open replay runner, saved result/source artifacts and closed paper-trade account/strategy projections. Qualified point-in-time evidence, registered-strategy replay, broader execution models and marked/live analytics remain pending. See [research and analytics](RESEARCH_ANALYTICS.md).

## 1. Workstation foundation — implemented locally

**Goal:** an owner can start the development stack, sign in, arrange a compact dark workspace, change a setting and inspect its persisted history after a restart.

### Implementation

- Create a Vite/React/TypeScript frontend, FastAPI backend, PostgreSQL service and Caddy frontend/API routing, with locked dependencies and a development Compose file.
- Use one owner identity with an explicit initial setup command, a password hash and expiring server-side cookie sessions. Bootstrap runs locally or through a protected administrator command; do not expose unauthenticated account creation. Use HttpOnly cookies, appropriate Secure/SameSite settings and CSRF protection for browser writes.
- Add schema-driven settings, deterministic resolution, revision history, reason/actor tracking and restore-as-new-version. Start with interface density, timezone, workspace preferences and worker concurrency. Later trading keys use the same service.
- Create a resource reference/resolver and an audit feed. Store server timestamps in UTC; render London time by default.
- Build the shell, health/status strip, docked panels, Settings and detail drawer. Support drag, resize, tab, maximize, close/restore, save, clone and reset layouts. Reset appends audit history and does not delete configuration history.
- Store layouts server-side per owner/workspace. Provide an unsaved indicator and explicit save; use version checks to prevent overwriting edits from another browser tab.
- Use honest empty states for accounts, orders, market data and agents. Empty chart panels offer connection/setup guidance; do not fabricate balances, signals or LIVE labels.

### Modules and migration

Create `frontend/`, `backend/` and `deploy/`; keep architecture documents in `docs/`. Backend modules initially cover identity, configuration, audit, workspaces, resources and health. The initial migration creates owner/session tables, setting definitions/versions, workspaces/layout versions and append-only audit records. There are no existing databases to migrate.

### API behavior

- `/api/v1/session`: owner login, logout and session inspection; login failures are rate-limited and redact inputs.
- `/api/v1/settings`: definitions, effective settings for a permitted context, revision history, validated updates and restoration. Require an expected revision; a stale update returns a conflict.
- `/api/v1/workspaces`: workspace and layout persistence, cloning and restore with revision checks.
- `/api/v1/audit`: authenticated, paginated history with resource references.
- `/health/live` and `/health/ready`: distinguish process reachability from database/application readiness; expose only minimal information publicly.

Generate frontend types/client from OpenAPI; financial contracts added later will use decimal strings. Add a basic authenticated change stream only when the corresponding panels need it; quote streaming belongs to the next milestone.

### Acceptance tests

- Unauthenticated API requests cannot read settings, layouts or audit data. CSRF-invalid writes and expired sessions fail.
- Two conflicting setting/layout writes cannot silently overwrite one another. Invalid values produce useful field errors and do not create revisions.
- Restore creates a new revision; the prior revision and audit event remain readable. Attempted audit mutation fails.
- Saving a layout/settings, restarting services and reopening the browser preserves values and shows their source/revision.
- Dragging, resizing, tabbing, maximizing, restoring and keyboard navigation work at desktop sizes; narrow screens remain usable.
- A fresh Linux development deployment reaches readiness; a missing/unavailable database returns an honest failure rather than an empty successful response.
- Production frontend build and backend checks pass. Browser tests exercise owner flows rather than merely compare implementation details.

**Expected output:** a verified development foundation and recorded checks. It contains no trading, broker secrets, live orders, fabricated agent output or full-release installer. It must not be called ready for autonomous trading.

## 2. Market data and native analysis — partially implemented locally

Add instrument/provider registries, OHLC/quotes, data health, timestamp/provenance handling and authenticated chart updates. Implement the chart adapter, resource-linked overlays, inspector and symbol linking. Use provider credentials supplied through setup, and visibly distinguish fixtures from connected data.

Integrate pinned Tauric in an isolated worker. Store original reports, structured fields where available, participating agents, model identifiers, evidence references and effective configuration. Build compatibility tests for streaming, failure, restart and instrument context. Implement and validate FX, intraday and futures extensions before enabling those analysis paths; test exact as-of timestamps rather than assuming a daily API is sufficient.

**Acceptance:** a real configured instrument can be charted and analyzed end to end; all reports and evidence remain readable; unavailable/ambiguous outputs block execution planning. Historical context cannot access later evidence. Fixture tests run without credentials, but do not count as proof of real connectivity.

## 3. Governance, models and paper execution

Package an independently versioned OpenClaw container, project plugin, skills and workflow definitions. Tools call scoped platform APIs. Persist validated governance as a separate linked artifact. Support cloud and external local endpoints, configured agent/model mappings, explicit fallback and model-call usage records. Test local tool calling and structured-output compatibility before routing critical tasks to a local model.

Add accounts, portfolios, capital pots, strategy versions/deployments, modes, account routing, Decimal sizing and inspectable risk calculations. Add shared order planning, paper fills, virtual ledgers and reconciliation. Implement applicable risk modules as configurable policies with declared input requirements. Unknown/missing required inputs cannot count as a pass.

**Acceptance:** a configured workflow requests Tauric analysis through OpenClaw, produces governance, plans and paper-executes a trade, then reconstructs its outcome from linked records. Repeated requests do not duplicate paper execution. Multiple strategies sharing an instrument maintain independent attribution. Unsupported modes/capabilities and tampering with finalized Tauric records fail.

## 4. Intelligence, research and observability

Current bounded research implementation includes the daily portfolio coordinator, saved costed replay and exact-version crypto breakout research-comparison qualification; see [qualification scope and evidence](STRATEGY_QUALIFICATION.md). Opt-in autonomous paper dispatch also has synthetic restart/exit/reconciliation evidence in [autonomous paper verification](AUTONOMOUS_PAPER.md). These completed paths do not establish point-in-time AI replay, qualified live scalping, strategy promotion or live broker execution.

Add configured intelligence feeds, editable entities/themes/relationships, catalyst and rumour lifecycle, scanners, on-demand chart explanations, manual deep investigation and configuration proposals with diffs. Scanning is deterministic screening; expensive analysis is a bounded queued stage. Expose workflow/job status and errors.

Add the point-in-time replay/backtest runner, persisted research datasets/results/artifacts, journal projections, account/strategy analytics and event-driven web/Telegram/Discord notification adapters. Notifications have configured recipients and channels; tests must use dedicated destinations, and this implementation work does not authorize sending unsolicited messages to people.

**Acceptance:** each selected feature has a real configured path, editable inputs and auditable outputs. Feed outages, future timestamps, duplicate items, disputed rumours and provider rate limits are visible. Research cannot mutate a deployed strategy version. Explain calls run only on explicit action or configured dwell, within configured budgets.

## 5. Four broker integrations and autonomous execution

Implement OANDA v20, IBKR through its supported API/Gateway, Alpaca Trading API, and separate Binance spot/futures capabilities. Every adapter implements account/instrument discovery, quotes where supported, orders, cancels, execution events and reconciliation. Keep product-specific contracts in adapters; the common contract must not imply identical broker behavior.

Cover market/limit/stop/protective orders where the broker/product actually supports them, contract increments, partial fills, cancellations, expiry, disconnects and external manual orders. Futures coverage includes supported contract families, position modes, margin, funding, leverage and liquidation-related account data; expose the verified coverage matrix. Do not label all futures supported after implementing one product family. IBKR login/session renewal and market-data entitlements are deployment requirements, not unattended behavior to invent.

Enable autonomous execution per account/deployment after configuration, capability checks and reconciliation. There is no routine per-order approval in the chosen autonomous policy. An emergency halt and protective-order handling must remain available. Unknown order submission outcomes suspend retries until resolved.

**Acceptance:** adapter contract suites, broker demo/paper/testnet workflows where available, retry/race/crash scenarios, account/virtual-ledger reconciliation and instrument sizing tests pass. Real account connectivity and entitlements are checked separately. Money-moving live tests require explicit authorization for concrete instrument/size/account tests; placeholders or simulated responses cannot certify live execution.

## 6. Installation package and full release gate

Deliver versioned images, release manifest, production Compose profiles, Linux preflight, owner setup, private secrets, persistent storage, backups/restore, health diagnostics, upgrade and rollback instructions. Keep OpenClaw and optional IB Gateway/model endpoints independently configurable. Do not automatically upgrade the host or upstream runtimes.

Measure memory, CPU, queues, query latency and storage growth on M+ with bounded concurrent workflows. Build images before deployment; do not compile the whole dependency stack on the small production host. Full-stack testing may establish that M+ is too small; report the measured requirement rather than silently disabling required features.

**Acceptance:** see [Server readiness](SERVER_READINESS.md). A successful Compose start, attractive dashboard or passing fixture suite is insufficient to mark the full release installable.

## Verification and progress reporting

Maintain a requirement checklist mapping the brief's substantive capabilities and the owner's additions to the milestone, evidence and remaining gaps. Report implemented behavior, configuration/migrations, checks, limitations and the next unresolved dependency at each milestone. Mark stages pending, in progress or verified accurately; do not claim a full brief requirement is complete because an empty panel or adapter interface exists.

Use unit tests for financial/configuration correctness, PostgreSQL integration tests for constraints/transactions, adapter compatibility tests, browser tests for user workflows, and dedicated Linux deployment/recovery tests. Pin tool/runtime versions and keep a tested upgrade matrix. These plans are acceptance criteria, not tests that have already run.

Development 0.10.0 adds saved per-deployment paper monitors and an independent bounded worker. Scheduled current-candle evaluation, attributed exit detection, concurrent-worker serialization, interruption recovery, worker/API restart persistence, provider-error records and browser controls are verified. Signals still require existing owner actions; autonomous execution and scanner scheduling remain pending. See [schedule verification](STRATEGY_SCHEDULING_VERIFICATION.md).


Named scanner profiles now support discovery without strategies or paper accounts, with optional versioned strategy links, independent histories and per-profile research allowances. Existing evidence and charges survive migration. See [independent scanners](INDEPENDENT_SCANNERS.md).

## Automatic demo broker routes — 4 October 2026

Owned opt-in OANDA practice / Alpaca paper routes now connect completed-candle signal discovery, account-equity/stop-distance sizing, exact native approval, bound OpenClaw approval, one-shot broker delivery, actual position tracking and attributed channel exits. Pause/settings-change guards, stable request IDs, permanent signal deduplication and separate worker lanes cover recovery. This remains demo-account scope. The starter breakout preset is explicitly unqualified; connected model-to-fill-to-close validation and broader full-release/market-intelligence acceptance remain unfinished. See [automatic demo routes and verification](BROKER_AUTOMATIC_ROUTES.md).
