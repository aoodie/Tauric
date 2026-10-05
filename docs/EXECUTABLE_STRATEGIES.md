# Executable paper strategies

The first executable family is `closed_bar_breakout_v1`, a long crypto spot strategy with manual and opt-in scheduled evaluation. Saved versions contain typed rules; deployments keep their exact opening version. Existing manual versions remain manual. Development 0.10.0 adds opt-in scheduled evaluation and attributed exit monitoring; autonomous execution remains pending. See [scheduled monitoring](STRATEGY_SCHEDULING_VERIFICATION.md).

## Owner workflow

1. In **Settings → Trading → Add strategy version**, choose **Closed-candle breakout · long spot** under **Entry and exit rules**. Set the candle interval, entry/exit lookbacks, minimum volume ratio, profit target in R and signal validity. Save the version with your capital, quantity step, risk limit and reason.
2. Deploy that saved version to an explicit paper account, allocation and registered BTC/ETH/SOL spot instrument.
3. In **Paper trading**, select the deployment and choose **Evaluate strategy rules**. Inspect ENTER, EXIT, HOLD or BLOCKED and its saved evidence. Evaluation itself makes no model calls and creates no orders or proposals.
4. For ENTER, the saved stop reference is populated and cannot be edited. Record the proposal, optionally linking original native research. Complete the existing owner/OpenClaw review, deterministic sizing, plan and simulated fill workflow. Automated OpenClaw reviews still require a valid original native report and their existing configured route.
5. For EXIT, choose **Close signalled paper position**. This explicit owner action closes only the position named by that signal. The observed 1-minute price, slippage and fees determine the paper fill. A halt or deployment pause blocks entries while allowing these attributed closes.

## Rule semantics

- A closed candle must finish strictly above every prior high in the entry lookback. Its exchange-base volume divided by that window's average must meet the configured minimum ratio. A forming candle never establishes a signal; unavailable historical volume cannot pass.
- The entry stop reference is the lowest prior low in the entry window. The proposal must use this exact saved reference; a price that has fallen below it cannot enter through the long paper planner.
- One open position per deployment. Repeating evaluation does not permit additional entries or a second proposal from the same entry candle, even after the first proposal was rejected or the position was closed. Wait for a new candle for a new entry proposal.
- The profit target is the actual entry fill price plus `take_profit_r × (entry fill price − opening stop reference)`. R excludes costs; the existing planner includes configured costs in its risk sizing.
- An exit passes if a complete post-entry candle touches the opening stop reference, touches the profit target, or closes strictly below the prior exit-window lows. Stop takes precedence when a candle touches both stop and target. A candle that began before the fill cannot establish an exit because its earlier OHLC range predates the position.
- Signal validity ends at the earlier of its configured expiry and the next candle boundary. Entry/exit use rechecks the original and current snapshot integrity, closed-bar evidence, pinned version, account ledger, and action/position references. Entry review/planning/filling also recheck deployment control revisions and existing paper risk/capital policies.

## Records and checks

Migration `0013_strategy_rules`, after `0012_research_analytics`, adds append-only evaluation records with owner/deployment/version/snapshot reference guards. The application role cannot update, delete or truncate them. A unique database index protects each deployment's single entry proposal per signal, and account locks serialize financial actions. Identical request retries return their original record; changed inputs conflict.

Evaluations preserve the exact closed-bar window, rule parameters, reasons, snapshot reference, strategy checksum, control revision, ledger digest, expiry and signal key. Proposals and opening plans/fills link the evaluation ID/checksum; closing plans/fills separately link the exit evaluation and trigger. Resource inspection and evaluation history remain available after reload.

Verification uses disposable PostgreSQL accounts and explicitly synthetic candles. The dedicated suite checks bounded inputs, authentication/CSRF, immutable evidence, version pinning, forming/future/stale/missing bars, exact Decimal exits, duplicate/concurrent proposals, expiry/evidence changes, and attributed exits with halted/paused controls. The built browser workflow checks rule editing/deployment, reviewed entry, persistence, exit/reconciliation and mobile layout without application errors. These checks certify mechanics, not strategy profitability or connected autonomous trading.

On 3 October 2026, all 21 dedicated rule checks, 217 backend regression checks and the built browser workflow (five recorded checks) passed. Frontend type checking/build, formatting and backend lint passed. The local Linux arm64 development images were rebuilt; a private database backup was saved before migration, `0013_strategy_rules` applied successfully, and the refreshed API/web/workers reached readiness. The local preview at `http://127.0.0.1:8080` serves the new entry/exit controls. No strategy evaluation or trade was created in the primary owner database. Backup restoration and full server-release checks remain separate, unverified gates.

```sh
python3 scripts/local.py test tests/test_strategy_rules.py -q
npm --prefix frontend run build
python3 scripts/local.py test tests/test_strategy_rules_browser.py -q
```

The optional browser test uses Python Playwright/Chromium, an ephemeral local server and a fresh test database. Its synthetic feed-control endpoint exists only inside that test; it is never registered in the normal application. Existing preview services and browser-test fixtures are not reset.

## Development upgrade and remaining scope

Rebuild the development API, migration and web images before applying the migration and restarting those services. The built-in readiness check now expects `0014_strategy_schedules`. Update native/governance/chat workers from the same source revision when refreshing the full development stack. No owner accounts, allocations, strategies or active deployments are seeded by the migration.

Remaining executable-strategy work includes autonomous policy dispatch, more rule families, broader risk modules and broker capabilities. Stop/target rules run on complete candles when requested or on an enabled schedule. Background monitoring is periodic closed-candle detection; it does not provide intrabar protective orders or guaranteed fills at a trigger price. Candle evidence is observed at receipt; historical point-in-time availability remains a separate acceptance gate.
