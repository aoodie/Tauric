# Tauric project gap audit — 6 October 2026

Tauric has a substantial engineering foundation. Its strongest features are versioned decisions, durable execution intents, exact evidence bindings, attributed positions, Decimal arithmetic, and recovery that avoids repeating uncertain submissions. The next investment should establish trustworthy performance measurement and a complete, observed trading cycle. That will make the existing breadth more valuable.

**Status update — 7 October 2026:** the findings below describe the 6 October code and evidence. Scanner/Strategies navigation has since been simplified, OpenClaw research management and reviewable upgrade proposals added, and local Honcho/Obsidian knowledge enabled. Trading work has added executable-side FX stop checks, attributed replacement protection, marked portfolio observations and prospective live-promotion gates. See [project management and RL readiness](OPENCLAW_PROJECT_MANAGER.md), [private knowledge and learning](KNOWLEDGE_AND_MEMORY.md) and [current trading readiness](LIVE_AUTOTRADING.md). These changes address several implementation gaps; real prospective strategy evidence, measured model contribution and target-host acceptance remain incomplete. The historical findings below should not be read as a claim that those implementation fixes are still missing.

This audit reviewed the current local code, requirements and verification records, deployment workflows, and saved desktop/mobile screenshots. It ran the offline backend suite against disposable databases and the local build and contract checks. It made no model calls or broker requests and changed no trading policies. Saved screenshots include earlier versions; UI findings were checked against current source where possible. Hosted release completion, connected trading and target-server operation were not independently revalidated here. Recommendations below are engineering judgments, not claims that a strategy will be profitable.

**Verified during this audit:** 888 backend tests passed; 18 browser tests were deselected; 11 deployment-control tests passed; backend lint, frontend formatting and production build passed; both OpenClaw fixture/contract checks passed. The saved OpenAPI specification exactly matches current source, including all 118 paths. A separate synthetic diagnostic reproduced the FX stop-model discrepancy described below. Passing existing tests does not cover that discrepancy.

| Priority | Gap | Evidence type | Next useful result |
|---|---|---|---|
| P1 | FX replay can miss an executable-side stop | Reproduced modeling discrepancy | Bid-side gap and intrabar stop checks |
| P1 | Alpaca partial closes can leave a remainder without protection | Confirmed, disclosed behavior | Durable replacement protection and failure recovery |
| P1 | Performance omits open-position valuation | Confirmed capability gap | Marked equity, drawdown and consolidated exposure |
| P1 | Trading edge and AI contribution lack measured proof | Validation gap | Frozen prospective comparison against baselines |
| P1 | Replay, qualification and broker execution need one validated lifecycle | Design/validation gap | Exact-version forward evidence and promotion gates |
| P1 | Complete connected and server recovery evidence remains incomplete in project records | Recorded verification gap | Full qualifying-signal cycle and target-host rehearsal |
| P2 | Model latency and cost are weakly connected to signal economics | Design gap | Stage latency, expiry and cost measurements |
| P2 | Capability labels and documentation have drifted | Confirmed source discrepancy | One capability/status contract |
| P2 | Workflows give configuration more prominence than decisions | UI review | Compact operating summary and action inbox |
| P2 | Worker health does not establish successful trading progress | Confirmed observability gap | Sanitized errors, progress metrics and actionable alerts |
| P2 | Large frontend bundle and broad background polling | Build/source evidence | Page loading boundaries and visibility-aware reads |

P1 means the next proof and correctness milestone; P2 means an improvement to operating and maintaining the project. These priorities do not replace the agreed full-platform requirements.

**1. Correct the FX replay's stop semantics first.**

The FX feed retains midpoint, bid and ask candles, and entry/exit pricing uses those sides. The SMA replay nevertheless detects stop gaps using midpoint open and intrabar stops using midpoint low. A synthetic, validated dataset entered long with a stop at 1.1088. A later bid low reached 1.1087 while the midpoint low remained 1.1090. The engine missed the stop and liquidated at dataset end, reporting a positive 15.8994 result. This is a retrospective replay issue; it does not demonstrate a defect in broker-held stops.

The limitation is already disclosed in the replay assumptions, which say triggers use reference OHLC. It remains unsuitable for comparing the replay with an executable bid-triggered stop. Use the intended trigger side consistently for detection, price estimation and risk; retain explicit ambiguity where OHLC cannot recover the price path. Add focused cases for spread widening, bid-only touches and opening gaps. [OANDA's order definitions](https://developer.oanda.com/rest-live-v20/order-df/) distinguish bid/ask trigger conditions and default executable sides.

Evidence: [replay_engine.py](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/replay_engine.py:118), [asset_costs.py](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/asset_costs.py:12), [declared assumptions](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/research.py:361), [reproducible diagnostic inputs and results](/Users/aoodie/Documents/Codex/Tauric/artifacts/project-audit-fx-replay.json).

**2. Complete protection management around Alpaca closes.**

An Alpaca manual close cancels active protective children before submitting the close. A partial close leaves shares behind without replacement protection. The interface explicitly warns about this, and the partial/final close test verifies quantities and attribution. The implementation has no automatic replacement-protection step. A rejected or uncertain close after cancellation also deserves explicit recovery treatment.

Model this as a durable sequence: observe protection, cancel/reconcile, close/reconcile, protect the remainder, and verify actual broker coverage. Test lost responses and protective fills during each transition. Until that sequence exists, treat an unprotected remainder as an operating incident with a visible recovery action. Preserve attribution so external holdings are never included in the replacement quantity.

Evidence: [close coordinator](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/broker_positions.py:499), [current UI warning](/Users/aoodie/Documents/Codex/Tauric/frontend/src/trading/BrokerExecution.tsx:494), [partial-close test](/Users/aoodie/Documents/Codex/Tauric/backend/tests/test_broker_positions.py:204).

**3. Measure the account while positions are still open.**

Paper analytics reconstruct closed trades correctly, but realized balance and drawdown exclude open positions and their entry fees. A portfolio could show modest realized drawdown while holding substantial unrealized losses. Broker journals are also not a unified, fee-complete portfolio performance service.

Add timestamped valuation with cash, marked positions, realized/unrealized P&L, total equity, fees and financing. Surface stale or missing marks explicitly. Aggregate currency, instrument, account and strategy exposure, including observed external broker holdings. Currency pairs require currency-leg exposure: two different symbols can still share the same underlying currency risk. The broker routes' documented absence of additional owner caps makes exposure visibility especially useful; numerical caps should remain an explicit owner choice.

Once valuation is reliable, add appropriate benchmarks, deposit/withdrawal-aware returns and currency attribution. These underpin the portfolio views already queued after launch.

Evidence: [analytics scope](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/analytics.py:139), [broker sizing limitations](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/broker_risk.py:76), [existing portfolio UI backlog](/Users/aoodie/Documents/Codex/Tauric/docs/POST_LAUNCH_TODO.md).

**4. Establish whether the strategy and AI layer earn their costs.**

Frozen hypotheses, held-out windows, cost stress and data-reuse checks are already implemented. Those are valuable controls. Current thresholds still describe a research comparison; they provide no measured evidence that the configured strategy has a repeatable edge or that a suitable model verdict adds returns.

Extend this into prospective shadow comparisons using the same eligible signals: deterministic rules alone, the rules with native review, and the complete native/OpenClaw review path. Keep normal execution permissions intact; the comparison variants can remain simulated. Measure after-cost return, marked drawdown, exposure, turnover, missed signals, stage latency and AI cost. Track outcomes for rejected candidates as clearly labeled counterfactuals, separate from actual fills.

Add uncertainty estimates and multiple chronological evaluation windows. Record every parameter/model trial, including rejected trials, and account for repeated searches where applicable. Choose sample requirements based on the strategy's variability and holding period. [Bailey and coauthors' backtest-overfitting research](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) explains why selecting winners from many trials requires more scrutiny than one attractive holdout result.

Evidence: [qualification thresholds](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/qualification_contracts.py:17), [current qualification criteria](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/qualification.py:648).

**5. Validate the strategy under the execution model it will use.**

The breakout replay observes completed candles and fills exits at the following open. Broker routes attach protection that may fill inside a candle. Entry also happens after model reviews, rather than at the replay's next open. These are materially different trade paths. The qualification correctly keeps execution modes incomplete, and a passing research report grants no authority. Demo routes can run an explicitly unqualified preset.

Introduce a lifecycle with distinct research, shadow, broker-demo and eventual live statuses. Bind each transition to the exact strategy, execution model, data semantics and evidence version. Include fill/protection behavior, signal-to-fill latency and adverse execution scenarios. Keep demo experimentation available while requiring applicable forward evidence before later promotion.

Paper broker success cannot establish live fill quality. [Alpaca documents](https://docs.alpaca.markets/us/docs/paper-trading) that its paper environment omits latency slippage, queue position and market impact, and does not constrain fills to displayed liquidity. Measure these limitations explicitly when evaluating later deployment suitability.

Evidence: [breakout replay exit timing](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/replay_engine.py:213), [execution-mode boundary](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/qualification.py:730), [automatic demo route documentation](/Users/aoodie/Documents/Codex/Tauric/docs/BROKER_AUTOMATIC_ROUTES.md).

**6. Complete one observed automatic cycle and the actual server rehearsal.**

Project records contain a real OANDA entry, verified stop/target and attributed close. They distinguish this integration check from the complete automatic qualifying-signal → native assessment → OpenClaw approval → fill → exit path. They also distinguish container release rehearsals from operation on the target server.

Use the existing OANDA practice route as the first integration proof because a filled-cycle record already exists, then repeat the applicable lifecycle on Alpaca paper. Later connected effects require their own authorization. Preserve exact source/image identities and reconstruct the outcome from broker receipts and the journal. Rehearse interruption at important boundaries, delayed fills and lost responses.

On the selected host, verify installation, restore, reboot, upgrades and representative resource use. A 24-hour soak should measure queue age, reconciliation delay, provider failures, memory and storage growth. Keep IBKR and Binance spot/futures execution as explicit remaining full-platform work; a narrower proof milestone should not silently remove that scope.

Evidence: [connected practice record](/Users/aoodie/Documents/Codex/Tauric/docs/BROKER_AUTOMATIC_ROUTES.md), [server verification record and open gates](/Users/aoodie/Documents/Codex/Tauric/docs/SERVER_DEPLOYMENT_VERIFICATION.md), [release criteria](/Users/aoodie/Documents/Codex/Tauric/docs/SERVER_READINESS.md).

**7. Match research latency to the signal's lifetime.**

Native analysis defaults to a 1,200-second timeout. The documented demo preset uses 15-minute candles and a 900-second signal lifetime, and still needs a separate governance review. This configuration does not prove that real reviews will miss signals, but it leaves that possibility unresolved. Daily native background and intraday setup evidence also have different horizons and, for stocks/crypto, different source feeds.

Record queue, data-fetch, native, governance and submission durations separately. Show median/tail latency and the proportion expiring at each stage. Track invocation/token usage and monetary cost where the provider supplies usable data; job-count allowances alone are not a spend estimate. Reuse daily context only with validated freshness and bindings. Preserve both reviews while selecting horizons and resource limits supported by measurements.

Evidence: [analysis timeout](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/config.py:28), [signal lifetime contract](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/strategy_contracts.py:24), [daily source scope](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/native_scope.py:23), [job-count budget](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/admission_budget.py:27).

**8. Give capability status one source of truth.**

The sidebar unconditionally says “Broker trading disabled,” although broker-connected practice/paper routes exist. The qualification introduction says crypto only, while the current backend and dataset selector support FX too. The README still lists scheduled broker routes as pending, while the newer automatic-route document describes their implementation. This makes progress and operating mode unnecessarily hard to interpret.

Create one capability contract keyed by asset, provider, timeframe and execution mode. Distinguish implemented, configured, connected-tested and operationally validated. Drive badges and enablement from it, and generate the coverage document from the same record. Show simulated paper, broker practice/paper and live as separate modes.

Evidence: [hardcoded broker status](/Users/aoodie/Documents/Codex/Tauric/frontend/src/App.tsx:1520), [stale qualification introduction](/Users/aoodie/Documents/Codex/Tauric/frontend/src/research/Qualification.tsx:263), [current FX eligibility](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/qualification.py:123).

**9. Make the workstation answer the operating questions immediately.**

The dark workstation identity, docks and trade lineage are worth preserving. Saved screenshots show large configuration forms, long explanations and technical record identifiers competing with useful summaries. Mobile pages stack controls correctly, but key actions can sit far down a long setup flow.

Put a compact summary above detailed controls: account and mode, marked equity, exposure, active strategies, protection coverage, data age, and items needing attention. Add a decision inbox for fresh candidates, pending approvals and failures, with a short reason and one next action. Move infrequent configuration into expandable settings. Make lineage a clickable trade timeline that opens the existing original evidence. Use readable names in primary views and retain identifiers in details.

Centralize timestamp formatting around the saved display timezone; current pages mix browser-local time, hardcoded London time and the workspace preference. Test essential workflows with keyboard navigation, narrow screens and zoom. Treat the screenshot review as design evidence, not a fresh browser certification.

Evidence: [automatic-route screenshot](/Users/aoodie/Documents/Codex/Tauric/artifacts/broker-routes-desktop.png), [mobile broker controls](/Users/aoodie/Documents/Codex/Tauric/artifacts/broker-execution-mobile.png), [qualification screenshot](/Users/aoodie/Documents/Codex/Tauric/artifacts/qualification-desktop.png), [workspace timestamp formatter](/Users/aoodie/Documents/Codex/Tauric/frontend/src/App.tsx:118), [broker timestamps](/Users/aoodie/Documents/Codex/Tauric/frontend/src/trading/BrokerRoutes.tsx:334).

**10. Observe progress and incidents beyond a heartbeat.**

Several worker loops catch broad exceptions and emit a generic reconnect message. A recent heartbeat can coexist with repeated work failures. Storage readiness is intentionally narrower than trading readiness. Secret redaction is useful, but the operator still needs a diagnosis.

Add sanitized error categories, correlation IDs, per-lane success/failure counters, oldest queued-work age, stale-position counts and unresolved-order counts. Record when protection is absent or reconciliation stops advancing. Give exits and protection recovery their own time budgets so a slow entry/provider path cannot delay attention indefinitely. Extend health with actual progress, keeping database readiness separately named. Owner alerts should cover meaningful incidents; notification delivery needs verified transport and failure handling.

Evidence: [broker worker lanes and generic errors](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/broker_worker.py:32), [paper monitor errors](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/strategy_scheduler_worker.py:31), [storage readiness](/Users/aoodie/Documents/Codex/Tauric/backend/tauric/app.py:114).

**11. Improve maintainability and client efficiency incrementally.**

The frontend build produces a 1,069.22 kB main JavaScript chunk, 302.68 kB compressed, and emits the large-chunk warning. App.tsx has 2,263 lines; PaperTrading.tsx has 1,268. Market, paper and scanner providers wrap the signed-in application and continue periodic reads across pages. These measurements identify improvement opportunities; this audit did not measure user-perceived startup delay.

Load major pages on demand, retain a small shared operating summary, and pause detailed polling when the page or browser tab is inactive. Separate dense forms, resource inspectors and domain hooks as they are next edited. Keep the modular Python application and PostgreSQL queues unless measured workload justifies a more complex architecture.

CI currently excludes browser tests. Add a small critical-path browser set for mode/status accuracy, approval expiry, interrupted actions and protection state, alongside the existing offline tests. Preserve the verified schema agreement and make drift checking part of the release gate. Avoid adding tests that merely repeat formatting or implementation details.

Evidence: [page imports](/Users/aoodie/Documents/Codex/Tauric/frontend/src/App.tsx:1), [global providers](/Users/aoodie/Documents/Codex/Tauric/frontend/src/App.tsx:2253), [paper polling](/Users/aoodie/Documents/Codex/Tauric/frontend/src/trading/PaperTrading.tsx:78), [CI coverage](/Users/aoodie/Documents/Codex/Tauric/.github/workflows/checks.yml:33).

**Useful strategy and product experiments after the correctness work:**

- Compare the existing breakout family with an appropriate passive/cash baseline and a simple slow-trend rule at matched costs and exposure. This gives the current implementation a measurable reference.
- Test whether session, spread, volatility and sourced-event filters improve the existing breakout after costs. Register each hypothesis before evaluating it. FX tick activity and exchange-traded share/coin volume need separate interpretations.
- Add a second strategy family only after its distinct behavior and portfolio contribution can be measured. Breadth across symbols alone does not establish diversification.
- Build an AI-value report showing which reviews changed decisions, what those decisions cost and their later outcomes. Evaluate candidate ranking against observed forward results before interpreting a score as a probability.
- Make a data-coverage map and incident replay view from existing receipts. These turn the project's audit trail into practical daily tools.

**Recommended implementation order:** correct executable-side replay and protection recovery; add marked valuation and incident visibility; freeze one strategy and run prospective comparisons; complete the connected automatic lifecycle; rehearse the exact release on the target host; then expand strategy families, brokers and portfolio drill-downs through evidence-based milestones. Keep the native research and governance architecture, full broker scope and existing post-launch commitments intact.
