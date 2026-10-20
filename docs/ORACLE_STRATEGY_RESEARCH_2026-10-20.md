# Oracle strategy research and OANDA execution readiness

Connected acceptance recorded 20 October 2026. Oracle is the active workstation at [tauric.aoodie.xyz](https://tauric.aoodie.xyz). Application source `8784660ea875050f6fcaf29efb2fede10e197120` was installed through the normal release process after [exact-source release validation](https://github.com/aoodie/Tauric/actions/runs/39389718168). Research is operating. Broker trading is not ready or enabled.

## Verified research operation

The installation has 12 healthy application services and two separate, source-bound research workers. Qlib runs the locked Python 3.12 environment with pyqlib 0.9.7 and LightGBM 4.6.0. LEAN uses `quantconnect/lean@sha256:7a72e2b753bada616422d47ddb80857e9544d1d68210fa86fe453e482a39833b`. The financial execution signature remains `broker_protected_long_v2`, checksum `7f0c0ef18c6f315af137f04f4fc98499b4d676774cc10fbbd2b05d4f461a9b01`.

OANDA supplied 180 days of EUR/USD midpoint/bid/ask prices: 3,072 hourly candles and 767 four-hour candles. The saved coverage has no detected missing bars. Equivalent repeat requests loaded zero provider pages. A separate process disabled the OANDA collection path and successfully created a normal study from the saved prices, demonstrating offline reuse. This does not establish historical point-in-time vintages or tick-level execution fidelity.

Automatic exploration is enabled for EUR/USD with hourly and four-hour scope, higher-timeframe context, mixtures, earlier walk-forward comparison and Qlib. The saved ceiling is two hypotheses and six automatic engine jobs per UTC day, with a 24-hour schedule. The settings are adjustable in Settings → Research. Paid idea generation remains disabled at a USD 0 daily ceiling; enabling it requires a positive owner-saved budget and token prices.

The first automatic cycle `5cd373b3-fcbc-493e-af4d-3102566ef351` finished all six engine jobs and rejected both hypotheses:

| Hypothesis | Screening return | Stressed return | Closed simulated trades | Outcome |
| --- | ---: | ---: | ---: | --- |
| Trend following, hourly | -0.8262% | -1.0244% | 10 | Rejected |
| Breakout, hourly with four-hour context | 0.0000% | 0.0000% | 0 | Rejected |

The rejected results remain immutable. No final confirmation or native qualification was claimed. The earlier failed EUR/USD trial is not reclassified or replaced.

Separate connected acceptance jobs verified Qlib training on 1,828 samples, model handoff to LEAN, and a half-trend/half-mean-reversion LEAN strategy with four-hour context. The mixture produced 16 attributed simulated fills and a -0.1991% return. The model handoff produced no fills. These are engine integration checks, not evidence of a profitable strategy or promotion eligibility. Saved job and dataset checksums bind their actual artifacts.

Research & analytics → Strategy discovery separates strategy explanations, results, saved prices, activity and research papers. Result inspection provides separate equity, drawdown and price/trade charts. The hosted workflow now requires the new browser workflow and three actual quant-runtime workflows; fixture screenshots are not presented as real OANDA performance.

## Connected execution assessment

A read-only probe at 18:18 UTC verified the configured OANDA practice account, EUR/USD currency-product classification and quantity/price increments, a fresh executable bid/ask quote, zero open positions and zero pending orders. It made five GET requests and no broker mutations. This verifies account/data access, not an actual protected entry or exit.

Oracle currently contains zero saved executable strategy versions, qualification protocols/reports, broker policy versions, routes or order intents. The broker execution, valuation and forward-validation services remain disabled. Native analysis and governance services are available; available services do not prove a completed native/OpenClaw trading decision.

The remaining gates are sequential:

1. Select a strategy that survives the recorded research criteria and fresh final evidence. Research definitions are long/flat studies; they do not automatically become native broker strategies. Trend, mean-reversion, mixtures and higher-timeframe plans need exact native parity before they can execute. The current native family remains `closed_bar_breakout_v1`.
2. Freeze the exact executable version and unused chronological qualification window, run baseline and stressed costs, and obtain the required native assessment. Do not lower trade-count, return or other agreed criteria to turn a rejected result into a pass. Reusing an inspected screening window cannot create fresh holdout evidence.
3. Record an owner-authorized practice execution policy and route, then enable the required services within that scope. An account's broker-side trading permission is not Tauric's execution authority. Preserve explicit pauses and financial ceilings.
4. Verify an actual governed signal → native assessment → OpenClaw review → protected practice fill → attributed exit → reconciliation. OANDA requests already attach `stopLossOnFill` and `takeProfitOnFill`; `clientExtensions` supplies correlation identity, not stop protection. The [OANDA order definitions](https://developer.oanda.com/rest-live-v20/order-df/) describe these distinct fields. A stop order is not a guarantee against gap/slippage loss.
5. Collect the registered prospective evidence, including at least 14 elapsed days and 20 closed trades under the agreed protocol, current valuations, cost/latency checks and complete reconciliation. Only then can a fresh passing report support a separately explicit live activation. Neither this research schedule nor the project ownership mandate authorizes that activation.

No strategy from the first cycle can progress through those gates. The automatic research schedule continues within its saved allowance; reaching today's ceiling does not authorize an extra search or a weaker test.

## TradingView MCP and LuxAlgo

The owner specified TradingView MCP only. No direct TradingView desktop or browser automation forms part of Tauric's acceptance. The connected chart MCP session identified Nasdaq futures, and a quote request for `OANDA:EURUSD` echoed that symbol while returning CME futures metadata and prices. That response was excluded from Forex evidence. The requested symbol alone does not establish the returned market's identity.

The session supplied no verified LuxAlgo strategy result. Script names containing “LuxQuant” do not establish LuxAlgo provenance or usable performance. The application's existing official TradingView MCP connection supplies bounded economic calendar and rate reads; it is separate from the host's chart MCP session and is not a persistent LuxAlgo strategy feed. No new external subscription or chart automation was enabled.

Any usable external idea must retain its source, actual market/timeframe, rules, receipt time and limitations, then be evaluated on Tauric's saved OANDA data. [LuxAlgo's screener documentation](https://docs.luxalgo.com/platform/ai-backtesting/fetching-strategies) states that its displayed backtests omit commissions/slippage and stop-loss/take-profit assumptions. Vendor results therefore cannot substitute for Tauric's exact cost and protection tests.

## Recovery and preservation

The independent installation check verified the loaded source and image identity, matching research source inventory, 42 protected financial/configuration tables and private configuration. Financial execution authority remained unchanged, and no AI idea call or broker order was made.

The application backup restored schema `0037_research_discovery`, all 85 table hashes and four runtime volumes in an isolated project with background services stopped. A separate research archive restored and verified all 685 source/control/artifact/unit files. Application archive SHA-256: `32f95453056f248cf076522202ca4d2cd3d7a5fdc6c2a0675611b51ddb15041e`. Research archive SHA-256: `bf31a9c3b00478c59472869af91dc38060126d131ab746ec9a3df145f7728234`.

Backup restoration exposed a recovery defect: Compose service start traversed dependencies and attempted an obsolete migration container, preventing the current services from restarting. The current application containers and research workers were recovered without changing images or financial settings. The follow-up source fix captures the original running container identities before stopping them, resumes that exact set, and attempts engineering recovery even when application recovery reports a failure. Local verification covers successful backup, dump failure, both recovery failures and invalid/missing container identity. Deployment of that follow-up fix is a separate source/release check; this record does not claim it is already installed.

Acceptance receipts are retained in private Oracle control records and local ignored `artifacts/oracle-discovery-*.json` and `artifacts/oracle-oanda-readiness.json`. These observations do not establish complete autonomous project ownership, a representative workload soak or scheduled off-host backup retention.
