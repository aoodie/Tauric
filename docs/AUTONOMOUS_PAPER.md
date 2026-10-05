# Autonomous paper execution

Development 0.11.0 adds opt-in execution for scheduled `closed_bar_breakout_v1` deployments. No policy is enabled or seeded by installation. It uses simulated money and the existing long crypto spot planner; it has no broker adapter or live-order authority.

## Owner workflow

1. Configure the chosen local/cloud AI connection and start native analysis and OpenClaw governance services.
2. Select an executable deployment in **Paper trading** and enable its scheduled monitor.
3. In **Autonomous paper execution**, choose automatic entries and/or attributed exits, the AI connection, daily research/review/entry limits, maximum report age and attempt budget. Save with a reason. Exit-only operation requires no model admission.
4. Inspect service readiness, today's usage and each attempt's saved signal, original reports, review and fills. Configuration changes and receipts remain in **Load paper execution history**.
5. Pause entries with the deployment control and resume when ready. A blocked attempt stays terminal; resume needs a fresh closed-candle signal and retains daily usage. Disable autonomous execution to revoke pending authority. Disabling monitoring also stops future action. Account halt and deployment pause block entries; separately enabled attributed exits remain available. Owner closes remain available after disablement.

Limits are per deployment per UTC day, across settings revisions. Research limit zero permits reuse only; review limit zero prevents new automatic reviews. Native jobs and OpenClaw reviews retain their existing queue/resource limits and selected connection. There is no model fallback. The original signal deadline caps the attempt even if its configured time budget is longer.

## Authority and evidence

Automatic entry requires fresh current-day, current-connection native research with all nine preserved report fields, matching job/request metadata, an intact original checksum and the original `BUY` rating. HOLD, SELL, missing, stale, interrupted or unverifiable evidence blocks progression. OpenClaw can reject or defer a native BUY; it cannot turn adverse native evidence into permission. Review must be from the workflow's actual bound OpenClaw job, with retrieved context and strict result identifiers. An owner-authored review cannot substitute for that permission.

Native research remains current daily USD context. It does not certify intraday setup assessment, USDT timing, exact-version strategy qualification or profitability. Separately pinned deterministic closed-candle rules supply paper timing/stop references, and the numerical planner enforces current account risk, exposure, quantity steps, shared capital and costs. Unsupported FX/futures and exact-version promotion remain separate acceptance gates.

Each attempt captures its immutable policy version, initial scheduled signal/checksum, ledger, capital, account policy/revision, deployment controls, model/configuration fingerprints and deadline. These are rechecked before research admission, proposal/review and financial writes. Changed authority or financial context blocks new action. Repeating a consumed candle cannot create another proposal or silently retry failed/interrupted model work. Only current daily research from the pinned native revision may be reused; separate strategy qualification jobs cannot replace it. Compatible current-day research may be reused; a failed matching run does not fall back to an older success.

## Durable processing

Migration `0015_autonomous_paper` adds immutable policy versions and stage receipts, plus durable workflow cursors. Database guards protect ownership, reference lineage, immutable admission fields, committed stage references, allowed transitions and terminal status. Request IDs and a deployment/signal/action uniqueness constraint prevent duplicate workflow admission.

The independent `autonomous_paper` service locks model queues before owner/account state. Model calls run in the existing leased workers; no model call is performed inside a financial transaction. Each stage, its receipts and any proposal/plan/fill commit together. Interruption or storage failure rolls back the stage; a restarted worker resumes committed work without duplicate financial records. Model-worker lease interruption ends that model job without automatic retry. Explicit API consumption of a bound plan/fill is reconciled into the workflow. A repeated committed request returns its original receipt after disablement; it cannot create another trade.

Fresh attributed exits take priority over waiting entries. Exit admission does not require entry capital-allocation eligibility. They validate the saved rule and named open position, then close only that position with the observed 1-minute paper price, configured slippage and fees. Closed candles and periodic checks are not intrabar protection or guaranteed fills at stop/target levels. The opening workflow later links the closing fill and reconciles the ledger even if entry authority has since been disabled.

## Verification scope

The dedicated PostgreSQL suite passes 47 cases on the combined source through migration `0019_market_adapters` (2026-10-03). It exercises an unattended research → bound review → entry → scheduled stop exit → journal path with independent Decimal cash/P&L reconstruction and new sessions between stages. AI responses and candles in that suite are explicitly synthetic. It also covers native HOLD/SELL denial, incomplete evidence, changed controls/routing/expiry, limits, review rejection/defer, native/review lease interruption, competing workers, precommit crash, storage failure/reconnect, provider outage, existing API fills, owner closes, immutable history and exit-only closure during halt/pause.

The frozen `0017_portfolio_research` regression baseline passes all 321 non-browser cases; two fixture-helper packaging failures were rerun successfully after restoring the helper. The shared baseline initially had one readiness failure while the concurrent migration names differed; the other chats resolved the chain as `0018_strategy_qualification` → `0019_market_adapters`. Combined release regression and preview deployment belong to the coordinating qualification chat.

The built browser flow verifies explicit exit-only policy save/disable/history, background attributed close, reload persistence and mobile layout. It uses synthetic candles, makes no model calls and retains ten recorded checks in `artifacts/strategy-rules-browser-results.json`.

The separate Linux container check passes real worker restarts while waiting for research, an API/worker restart with an open position, a subsequent closed-candle attributed exit, closed-workflow reconciliation and a restart after disablement. It reconstructs cash and realized P&L independently using Decimal, finds exactly one entry and one close, and confirms the original report is unchanged. Its candles and native/review outcomes are explicit fixtures; it makes zero model and broker calls. The disposable database, containers and temporary keys are removed. Evidence is saved in `artifacts/autonomy-container-results.json`; dedicated results are in `artifacts/autonomy-final-tests.log`.

A naturally positive unattended run using real native BUY research, real OpenClaw permission and real public entry/exit candles is not yet certified. Earlier real connected owner-reviewed paper verification remains documented separately in [Connected paper workflow](REAL_PAPER_WORKFLOW_VERIFICATION.md); its native HOLD result does not satisfy this new automatic BUY gate. Long-running soak, recovery from a restored backup and full Linux server installation remain separate gates.

```sh
python3 scripts/local.py test tests/test_autonomous.py -q
python3 scripts/local.py test -m 'not browser' -q
npm --prefix frontend run build
python3 scripts/local.py test tests/test_strategy_rules_browser.py -q
```

Build API/migration/web/native/governance/chat/monitor/autonomous worker images together after source changes. Back up before migrating and refresh all services from matching images. Native admission checks need the private OpenClaw routing fingerprint as well as their selected model connection; development Compose mounts the private file without publishing it. The autonomy migration never changes owner paper settings or original reports. See [Development](DEVELOPMENT.md) for disposable fixture cleanup and container verification.
