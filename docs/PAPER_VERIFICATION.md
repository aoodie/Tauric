# Paper execution verification — development 0.3.0

This records the development 0.3.0 owner-reviewed paper slice. Development 0.4.0 adds [queued OpenClaw reviews](GOVERNANCE_VERIFICATION.md); development 0.5.0 adds [shared capital allocations](CAPITAL_VERIFICATION.md). This historical record describes the initial paper mechanics. This is not autonomous trading or a server release. AI connections remain available for later configuration. There are no broker order endpoints.

## Working flow

In **Settings → Trading**, create a USDT paper account with an explicit starting balance and all risk, cost, exposure, position-count, quote-age and decision-expiry inputs. Create a versioned strategy deployment routed to that account and a registered BTC/ETH/SOL spot instrument. Set its capital limit, risk percentage and paper quantity step. No account balances or risk preferences are seeded.

In **Paper trading**, enter a long spot proposal with a USDT stop reference and reason. Link an original native report when available, or explicitly use an owner test proposal. The native report and rating remain unchanged; owner governance gets a separate immutable allow/reject/defer record. An allowed, current decision can generate an inspectable Decimal plan and a simulated fill. Close an individual attributed position explicitly. Workspace positions, fills and journal panels read the same ledger.

The account halt persists and blocks new entries while allowing owner closes. Policy edits create immutable versions, carry a reason and require the current account revision. Reports, decisions, plans and fills have resource details and integrity hashes. Older journal items remain addressable by resource ID; the state response currently returns the latest 200 records.

## Mechanics and limits

- Unlevered, long crypto spot only, in USDT. No shorts, partial closes, transfers, live orders, futures or broker contract certification.
- Quantity is floored to the owner-declared paper step. This is not a verified Binance lot filter.
- The entry model uses the latest observed 1-minute candle close, adjusted adversely by configured slippage; fees are applied to entry and exit. A forming candle is an explicitly receipt-stamped observation, not a completed bar or bid/ask quote. Price age must satisfy the user policy and the feed's 60-second freshness boundary. Gapped/missing/expired observations block planning.
- Size is capped by per-trade account and strategy risk, aggregate marked open-stop risk, remaining cash including entry fee, account exposure, strategy capital and account position count. Stop-risk calculations include modeled stop slippage and entry/exit fees. If an existing position crosses its stop reference, new entries are blocked until the owner closes it.
- **Stops are sizing references, not monitored stop orders.** There is no claim of guaranteed loss limits, fill liquidity, protective-order monitoring or automatic exits.
- Portfolio and capital-pot names preserve fill attribution. They do not implement nested entities or pooled capital budgets yet. Account/strategy configuration inheritance, routing across live brokers and the remaining configurable risk modules are pending.
- Plans capture decision, original native evidence when linked, policy version, account revision, ledger digest, market snapshot and price expiry. A change to any execution context blocks use of an old plan; start a new proposal.
- Account row locks serialize planning/fill validation and ledger writes. A plan fills once. Repeated identical request IDs return the original record; changed inputs conflict. Every exit consumes one opening attributed position. Same-symbol positions from different strategies remain independent.
- Cash, realized P&L and virtual positions are rebuilt from immutable fills. Integrity and ledger reconciliation failures block reads/execution instead of yielding successful empty data. Restricted SQL roles cannot mutate history or account identity. Administrator tampering remains possible but checksum mismatches are detected.
- Paper native report links verify owner, instrument and original checksum. Native daily USD research does not establish executable USDT/intraday prices. Owner proposals require an explicit USDT stop; no text parsing invents one.

## OpenClaw boundary

The independent runtime is pinned to **OpenClaw 2026.9.7** with npm dependency integrity locks and a Node 24.21.0 digest-pinned base. The project tool uses the supported [tool-plugin SDK](https://docs.openclaw.ai/plugins/tool-plugins); the tracked manifest, skill and workflow live in `openclaw/`. No upstream core is edited.

The sole optional tool, `tauric_paper_context`, reads immutable proposal, policy, deployment, paper balances and linked original native evidence from a private authenticated API. It has no order or report-writing tool. The Gateway receives separate private gateway/service tokens, no database credentials or broker secrets. Its port is not published; Caddy rejects `/internal/*`. API and Gateway copy host-private tokens into their own private service files and drop root privileges before service execution.

The Gateway's chat-completions endpoint, heartbeat model calls, cron scheduler and built-in memory agent are disabled in this slice. The owner UI deliberately reports AI governance as unconnected. A healthy Gateway and validated tool are not a verified model-backed workflow. Model mapping, bounded governance jobs, usage capture, cloud/local structured-output and tool-call certification, autonomous scheduling and a connected native → OpenClaw → paper run remain pending. The documented [Gateway HTTP interface](https://docs.openclaw.ai/gateway/openai-http-api) will be enabled only as that application path is implemented.

## Evidence

- 78 backend checks passed, including 24 paper cases covering authenticated/CSRF boundaries, exact decimal inputs, fees, size steps, idempotency, concurrent plans, policy conflicts, halt/close behavior, immutable records, same-symbol attribution, stale prices, invalid evidence, unchanged original reports and deliberate ledger tampering.
- 11 foundation and 5 owner-settings browser flows passed again after the change.
- 5 paper browser flows passed: explicit account/policy/deployment setup, actual public Binance prices, owner decision → deterministic plan → simulated entry, explicit exit with costs and reconciliation after reload, persistent halt and policy revision, and mobile layout without horizontal overflow/page errors.
- 3 additional browser regression checks passed: injected storage failure cannot appear as an empty ledger, recovery clears the availability error, and successful polling preserves an action validation error.
- The pinned SDK compiled and validated the plugin. An offline check uses its real registration/execution contract with fixture context and failure responses; it makes no model calls.
- 6 built-container workflows passed, including the actual private Gateway → registered plugin → isolated API context read without model calls, required tokens, disabled chat route and rejected public private-API path. Results are recorded in ignored `artifacts/docker-browser-results.json`. They cover production assets, private ingress, paper persistence, worker decryption and the separate Gateway/tool boundary. This record, not an image build alone, establishes which container checks passed.

Screenshots and result files are in ignored `artifacts/`. Test databases and owners are disposable; no real owner password, AI connection or trading balance is overwritten. Linux amd64/IONOS resource checks, backup/restore, rollback and a 24-hour soak remain outstanding. The full platform is not ready for server installation.
