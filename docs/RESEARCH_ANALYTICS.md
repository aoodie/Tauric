# Research experiments and paper analytics

Open **Research & analytics** in the sidebar. This first implementation provides isolated retrospective spot experiments and outcomes reconstructed from paper fills. It does not complete the broader intelligence or point-in-time research brief.

## Research workflow

1. Choose an instrument and interval in the market workspace. In Research, **Save chart dataset** freezes the closed candles of the selected saved snapshot. **Refresh chart data** explicitly requests a new public feed snapshot first when needed.
2. Alternatively, **Import candle JSON** accepts a registered BTC/ETH/SOL USDT spot instrument, supported chart interval and 3–5,000 continuous candles. The file needs `instrument_id`, `interval`, `source` and `candles`; exported dataset records can be imported again. Candle timestamps require timezones; OHLC/volume are plain decimal strings and `closed` is true. Invalid OHLC, incomplete/future candles, duplicate timestamps, overlaps and data gaps are rejected. Imports are labelled owner-supplied, unverified sources.
3. Choose a saved dataset and record an experiment name and hypothesis. Configure the fast/slow SMA windows, starting USDT capital, allocation/risk percentages, stop distance, quantity increment, fees and slippage. These parameters belong only to the experiment.
4. **Run replay** stores a completed deterministic result. Inspect the equity curve, closed trades, fill prices, fee-adjusted results and assumptions. **Export result** includes the original request, dataset checksum, algorithm source artifacts/checksum, all signals, fills, trades and curve points. Dataset and run records also use the common resource drawer.

Datasets and runs are append-only PostgreSQL records, protected against update/delete/truncate. An identical request identifier returns the existing result; changing its inputs conflicts. Owner locks serialize duplicate admissions. Runs must reference a dataset belonging to the same owner. Research has no paper-account, strategy/deployment editing, model or broker order path. History pages show 25 datasets and results at a time.

## Replay semantics

The initial algorithm is `sma_cross_spot_v1.0.0`: long-only unlevered spot with a single position. It observes only each prefix of closed candles, computes exact SMA cross comparisons and fills an eligible signal at the following bar's open. A final-close signal has no following open and does not fill. Stops reference the observed entry open. A stop gap fills at that bar's open; an intrabar low crossing fills at the stop reference with configured slippage. Intrabar execution time cannot be recovered from OHLC and is explicitly stamped at bar close. A remaining position liquidates at the final close with costs.

Sizing rounds down to the configured quantity step and applies both cash/allocation capacity and risk including entry/exit slippage and fees. Authoritative arithmetic uses Decimal with the paper spot entry/exit and risk formulas. Cash and closed results must reconcile. Replay monitors its simulated stops; ordinary owner-reviewed paper trades use stop references and explicit closes. Full-depth liquidity, bid/ask spreads, partial fills, exchange minimum notional, financing, leverage and futures are not modeled.

Return uses final liquidated equity. Replay drawdown uses running peaks of close-marked equity, including opening costs and final liquidation. It does not establish intrabar extremes. Undefined win rate, average trade outcome/holding time or profit factor are null, rather than fabricated zero or infinity.

## Availability boundary

All datasets are **retrospective**. A Binance snapshot records receipt time and its original checksum/provenance; capture does not turn old candle timestamps into evidence that the system observed them historically. Imported provenance is owner-supplied and unverified. The runner prevents later candles from influencing earlier signals, but no point-in-time news, model reports, economic releases or historically qualified provider dataset is enabled. It cannot replay the full Tauric graph historically.

The existing Binance OHLC fields and timestamp mapping follow the [official market API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market). The replay's execution rules are explicit project assumptions, not broker execution guarantees.

## Paper analytics

The **Paper analytics** tab rebuilds each account from its complete checksummed fill ledger while holding the same account lock used by execution. Closing fills are paired with their attributed opening fills; profit/loss is independently calculated from quantities, entry/exit prices and both fees, checked against the stored close and reconciled with the account ledger.

Results include cash, realized balance/return, closed-trade win/loss/breakeven counts, win rate, average trade P/L, average holding time, profit factor, all fill fees, realized drawdown and deployment/strategy-version attribution. Open position counts remain visible. Trade rows link to the original closing fill; exports preserve opening and closing references and native-report lineage. Different strategies sharing one instrument retain independent outcomes.

**Realized balance and drawdown cover closed trades only**; they exclude current holdings and open-entry fees. Cash includes all recorded fills and total fees include open entries. Unrealized profit/loss, total account equity and intratrade drawdown need historical marks and are not inferred. Profit factor is unavailable without a losing trade. No annualized or risk-adjusted statistics are inferred from a short or irregular sample.

## Verification

- Research/accounting API and engine checks: `python3 scripts/local.py test -q tests/test_research.py tests/test_analytics.py` — 22 passed.
- Full backend regression: 217 passed, one optional browser test deselected. Production frontend type check and build pass.
- Real public Binance BTC/USDT 15-minute dataset capture and replay verified on 3 October 2026 in a disposable database: 299 closed candles, 10 closed trades, source/configuration/result artifacts and exact outcome reconciliation. No model calls or broker orders. Run `.venv/bin/python scripts/research_browser_test.py --real-market`; sanitized evidence is in `artifacts/real-research-results.json`. This verifies the data path and replay, not strategy profitability or point-in-time availability.
- Nine browser workflow checks pass, including dataset capture/export/import, saved results and reloads, curve inspection, explicit errors, populated paper outcomes/attribution, fill drill-down and desktop/mobile layout. Browser verification uses production assets and a separately disposable database: `.venv/bin/python scripts/research_browser_test.py`. It needs Python Playwright in the system Python and the local webapp-testing server helper. It never uses the shared governance/browser fixture, configured model or broker. Sanitized checks and screenshots are saved under `artifacts/research-*` and `artifacts/analytics-desktop.png`; temporary credentials/key/database are removed after the run.

Migration `0012_research_analytics` adds the research record table and immutable history/owner guards. The current later strategy migration remains part of the shared development schema. Follow normal migration-before-application ordering when updating the local containers.

Remaining research scope: qualified point-in-time datasets and evidence access, full native historical analysis, replay of registered executable strategy families, walk-forward/out-of-sample evaluation, parameter search/optimization, richer market/product execution models and durable queued research workers. Remaining analytics scope includes marked equity/unrealized outcomes, risk-adjusted returns and broker/live performance. Intelligence feeds, catalysts, rumours and notifications remain separate pending work.

## FX and stock data extension — 3 October 2026

OANDA FX and Alpaca US stock/ETF adapters now feed calendar-aware charts and immutable research datasets. Explicit asset costs, FX bid/ask prices and saved rollover events extend long-only SMA experiments. Source/feed, stock adjustments, session calendars, quality checks and quote-currency accounting are preserved. Connected provider verification requires credentials and entitlements; broader execution/native qualification remains unavailable for these assets. See [implementation and verification](MARKET_DATA_ADAPTERS.md).
