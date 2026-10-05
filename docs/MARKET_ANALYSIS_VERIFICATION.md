# Market and native-analysis verification

Current update: development 0.7.0 retains the real native daily BTC run with all nine original reports and adds a [crypto scanner](SCANNER_VERIFICATION.md) plus a [real bound governance rejection check](REAL_GOVERNANCE_VERIFICATION.md). See [connected AI verification](CONNECTED_AI_VERIFICATION.md) for its evidence and limits. The 0.2.0 record below preserves the historical results of that stage; its unconfigured-model statements describe the earlier state. Later paper, allocations and strategy stages are recorded in [paper verification](PAPER_VERIFICATION.md), [capital verification](CAPITAL_VERIFICATION.md) and [strategy verification](STRATEGY_VERIFICATION.md).

Recorded 3 October 2026 for development version 0.2.0. Milestone 2 is partially implemented; its connected-analysis and instrument/time-context acceptance gates remain open. The full platform is not ready for server installation. Settings now provides Preferences, AI connections and Security tabs; model setup no longer requires editing the host file. Password changes require the current password and revoke all sessions.

## Implemented behavior

- Registered BTC/USDT, ETH/USDT and SOL/USDT Binance spot markets. Public REST snapshots contain up to 300 candles at 1m, 5m, 15m, 1h, 4h or 1d. Refresh is explicit, with an optional 30-second polling control. No account endpoints or order methods exist in this adapter.
- Decimal strings, provider/receipt timestamps, closed/forming flags, gap counts and SHA-256 integrity records. Invalid OHLC, nonfinite/negative values, old series, future timestamps, wrong durations, ordering errors and excessive clock differences fail before creating a snapshot. Saved data becomes stale after 60 seconds; freshness means a recent verified receipt, not a live websocket connection.
- Lightweight Charts 5.2.1 with symbol linking to analysis, crosshair OHLC/volume readout, six intervals, editable visual SMA, provenance drawer and TradingView attribution. Financial prices retain exact strings in storage; chart rendering uses JavaScript numbers. The SMA is a display reference, not an execution input. Instrument/timeframe/SMA choices reset on browser reload; snapshots and workspace geometry persist.
- Native TradingAgents 0.5.2, revision `8b22d43d01d9ddda5d686d093d5385884622f3de`, installed in a separate runtime. The source archive is pinned by SHA-256 `cd50792b6a41ff0830cd9149a9a8fd2e27100799257dd7d2147534574db39299`. The graph is preserved, including its four analysts, bull/bear research debate, research manager, trader, three risk analysts and portfolio manager.
- Cloud/external-local profile slots, private credentials, model/settings capture, durable idempotent requests, bounded queue, serialized worker leases, progress and immutable reports. Final reports preserve public native prose and debate histories; raw messages/provider reasoning traces are not exposed through the report API. No execution authority is produced.
- Database migrations through `0004_owner_settings`, including a corrective JSON comparison migration and encrypted immutable connection revisions. Runtime roles cannot alter request lineage or update/delete/truncate finalized records.

## Checks completed

| Check | Evidence |
|---|---|
| Backend PostgreSQL/auth/configuration/market/job checks | 54 passed; includes malformed provider responses, rate limits, profile redaction, concurrency/idempotency, interrupted leases and immutable artifacts |
| Existing workstation browser workflows | 11 passed; settings/layout history, docking, conflicts, outage/restart, mobile and logout |
| New market browser workflows | 5 passed using actual public Binance BTC and ETH data; chart/inspector/SMA/provenance, timeframe/symbol linking, both inactive model routes, snapshot reload and mobile |
| Native compatibility | Cloud and OpenAI-compatible clients compile the original 12-agent graph with network connections blocked; native crypto context and key mappings checked; zero model calls |
| Container build | API, migration, web and isolated native-worker images build in Linux arm64 through Docker Desktop |
| Owner Settings browser flows | 5 passed: keyboard tabs, cloud/local saves, key handling, conflict reload, password changes/all-session logout and mobile; no real model calls |
| Built-container checks | 4 passed for assets/Caddy headers, saved settings/workspaces/logout and encrypted connection access from the built worker |
| Runtime isolation | Actual API/worker processes run as UID 10001; copied model file owned by UID 10001 at 0600; worker runtime volume writable; fresh 0.5.2 worker heartbeat; both profile slots unconfigured |
| Static checks | Ruff, TypeScript/Vite production build, generated OpenAPI client and formatting checks pass |

Ignored local evidence includes `artifacts/market-browser-results.json`, `market-desktop.png`, `market-mobile.png` and `native-compatibility.json`. Browser testing uses disposable owner/database credentials and never signs into a broker account. Temporary credentials are removed after checks. Actual market checks require internet connectivity; deterministic backend fixtures do not certify connectivity.

The pinned upstream model catalog warns that the offline fixture model names are not in its known list; it still compiles. This confirms client construction only, not current model availability or account entitlement.

## Remaining limits

No configured model-backed graph run has completed: the owner chose to configure both routes later. Native data-provider connectivity, chosen-model tool calling/structured outputs, full report completeness, cancellation and checkpoint recovery under a real model run remain unverified. Chart and native research have different quote currencies/data sources and are explicitly labelled.

Historical point-in-time replay, intraday FX and futures extensions, streaming events, scanner, advanced overlays, broker adapters, risk/sizing, OpenClaw, paper execution, account ledgers and trade analytics remain pending. No broker order was submitted. This public data adapter is not a Binance trading integration.

Resource limits are development budgets. Runtime/filesystem snapshot growth has no retention policy yet, and M+ memory/CPU measurements, a clean Linux/amd64 deployment, backups/restore, upgrade/rollback and a 24-hour full-stack soak remain open. Native artifacts/checkpoints require private retention and eventual backup alongside PostgreSQL. A running container does not satisfy these release gates.

## Reproduce

See [Development](DEVELOPMENT.md) for backend/frontend/browser commands and [Model connections](MODEL_CONNECTIONS.md) for private setup and the optional worker. `scripts/native_compatibility.py` runs with the separate runtime environment, never with the minimal API environment. The same pinned runtime is built automatically in `deploy/Dockerfile.worker`.

Primary integration references: [Binance market-data-only endpoint](https://developers.binance.com/en/docs/products/spot/faqs/market_data_only), [Binance market API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market), [Lightweight Charts API](https://tradingview.github.io/lightweight-charts/docs/api), [pinned native graph](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/graph/trading_graph.py).
