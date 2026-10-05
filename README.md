# Tauric trading workstation

Self-hosted trading intelligence, governance and execution for one owner.

A [GitHub deployment candidate](docs/GITHUB_DEPLOYMENT.md) now provides gated checks/image publication, a digest-pinned server package, private setup, optional HTTPS, backup/restore and resource observation. An isolated Linux arm64 container rehearsal passed clean startup, owner login, saved settings, restarts and restoration of all 70 database tables and four runtime volumes. [Server package verification](docs/SERVER_DEPLOYMENT_VERIFICATION.md) records its limits; hosted GitHub runs, the actual Linux host and the 24-hour representative soak remain unverified.

The [Market intelligence workspace](docs/MARKET_INTELLIGENCE.md) provides asset-specific views of saved daily moves, personal watchlists, sourced headlines, supported policy meetings and eligible scanner candidates. Forex, stocks/ETFs and spot crypto use the existing data connections; commodity and rates views explicitly show the dedicated feeds still needed.

**Status: development 0.11.0 includes portfolio-bound OpenClaw conversations, versioned mandates, daily portfolio research and permitted paper controls, alongside scheduled strategy monitoring and verified autonomous paper recovery, attributed exits and pause/resume controls. The real connected approval-to-paper-entry/close path is verified. The full platform is not ready for server installation.**

The workstation includes owner login, a dockable dark workspace, saved layouts, versioned settings and an append-only activity record. PostgreSQL stores the data; the browser uses the actual API. BTC, ETH and SOL spot charts load real public Binance data. A real daily BTC analysis completed through the separately pinned native Tauric graph and saved all nine original report fields. The external-local connection uses an OpenAI-compatible endpoint with `ag/gemini-3.8-flash-high` for both roles; cloud remains unconfigured. Real OpenClaw model transport and one bound-context rejection review pass. Owner-reviewed paper proposals, Decimal sizing, shared allocations, attributed fills, explicit closes and reconciliation work. A fresh real bound approval-to-paper-entry/close workflow now passes independent journal reconstruction and API-restart persistence in a disposable daily BTC environment; OANDA practice and Alpaca paper manual broker orders are now verified through submission/cancellation; autonomous broker execution remains pending. See [connected paper verification](docs/REAL_PAPER_WORKFLOW_VERIFICATION.md). See [real governance verification](docs/REAL_GOVERNANCE_VERIFICATION.md). See [connected AI verification](docs/CONNECTED_AI_VERIFICATION.md).

Open **Research & analytics** for saved retrospective spot datasets, costed SMA replay, equity/trade results and account/strategy analytics from verified paper fills. See [research and analytics](docs/RESEARCH_ANALYTICS.md) for assumptions, verification and remaining scope.

## Local development preview

Requires Docker with Compose v2. Run from this folder:

```sh
python3 scripts/dev_setup.py
docker compose --env-file .env -f deploy/compose.dev.yml up -d --build
docker compose --env-file .env -f deploy/compose.dev.yml exec api python -m tauric.cli create-owner --username owner
```

Setup creates private database passwords and an empty private `.env.models.json` and a private model encryption key plus separate OpenClaw service tokens without displaying secrets. Owner creation prompts for your chosen password and never overwrites an existing account. There is no default owner password. Open [the local workstation](http://127.0.0.1:8080) and sign in.

This development profile uses local HTTP and publishes web/database ports only on the host's loopback interface. A persistent Docker volume holds the database. Stop services without deleting it:

```sh
docker compose --env-file .env -f deploy/compose.dev.yml --profile analysis --profile governance down
```

Do not add `--volumes` unless deliberately discarding the development database. See [Development guide](docs/DEVELOPMENT.md) for editing, checks and recovery limitations.

Open **OpenClaw chat** to talk to the assistant or ask “Run daily BTC research”. Research cards show actual job status and link to original reports. The shared daily run allowance is in **Settings → Chat connections**. Configure both bots in **Settings → Chat connections**; their real delivery awaits your bot tokens and numeric user IDs. Password changes remain in **Settings → Security**. See [OpenClaw chat setup and verification](docs/OPENCLAW_CHAT.md).

Choose a paper portfolio under **Conversation context** for its separate saved chat and workspace. Portfolio mandates, account allocation links and chat permissions can be saved there or in **Settings → Trading**. Permitted changes append durable action receipts. Configure automatic daily opportunity scans and recorded strategy experiments in the expandable research panel. Read-only OANDA FX and Alpaca stock/ETF adapters now support calendar-aware charts and explicit-cost retrospective SMA replay; connected feed verification needs credentials/entitlements. See [asset data adapters](docs/MARKET_DATA_ADAPTERS.md). Exact-version crypto research-comparison qualification now records held-out and stressed results plus a separate native strategy verdict; see [strategy qualification](docs/STRATEGY_QUALIFICATION.md). Forward execution qualification, promotion and managed transfers remain pending. See [daily research verification](docs/PORTFOLIO_DAILY_RESEARCH_VERIFICATION.md) and [portfolio verification](docs/PORTFOLIO_CHAT_VERIFICATION.md).

## Agreed release

- Full trading workstation, following the supplied brief's product goals.
- Autonomous execution under user-configured account and strategy policies.
- OANDA, Interactive Brokers, Binance and Alpaca integrations; an adapter contract for further brokers.
- Binance spot and futures.
- Cloud models and a separately hosted local model endpoint.
- Independently versioned OpenClaw service.
- Linux with Docker Compose; IONOS M+ for initial testing.

## Project documents

- [Stock workflow verification](docs/STOCK_WORKFLOW_VERIFICATION.md): connected AAPL/SPY research and feed checks, stock/ETF screening, bound reviewed whole-share paper entries, costs and attributed exits.
- [Daily portfolio research verification](docs/PORTFOLIO_DAILY_RESEARCH_VERIFICATION.md): durable schedules, both research tracks, shared admission limits, scoped chat controls and asset readiness.
- [Portfolio workspace and chat verification](docs/PORTFOLIO_CHAT_VERIFICATION.md): permanent portfolio conversations, versioned mandates/account links, permitted chat actions and their limits.
- [Portfolio and strategy research design](docs/PORTFOLIO_RESEARCH_DESIGN.md): agreed mixed portfolios, strategy research, capital modes and portfolio-scoped OpenClaw chat, with open choices recorded separately.
- [Portfolio implementation plan](docs/PORTFOLIO_IMPLEMENTATION_PLAN.md): current-code comparison, implementation order, acceptance checks and the first portfolio/chat slice.
- [Scheduled paper monitoring](docs/STRATEGY_SCHEDULING_VERIFICATION.md): opt-in background checks, recovery, status and immutable history.
- [Executable paper strategies](docs/EXECUTABLE_STRATEGIES.md): saved candle rules, reviewed entries, attributed exits and remaining automation work.
- [Architecture review](docs/ARCHITECTURE.md): existing state, upstream findings, UI review, gaps and design decisions.
- [Build plan](docs/BUILD_PLAN.md): implementation sequence, first slice and acceptance criteria.
- [Server readiness](docs/SERVER_READINESS.md): deployment assumptions and the conditions for calling a release ready.
- [Fresh connected paper check](docs/REAL_PAPER_WORKFLOW_VERIFICATION.md): real analysis, approval, paper entry/close, independent journal reconstruction and restart persistence, alongside earlier rejection evidence.
- [Foundation verification](docs/FOUNDATION_VERIFICATION.md): checks, evidence and limitations.
- [Requirements coverage](docs/REQUIREMENTS.md): implemented capabilities and remaining work.

The attachments are product and visual reference material. Instructions addressed to Codex inside the supplied brief are not treated as additional authorization or repository rules. The owner's choices in the conversation establish scope and take precedence over illustrative settings in the brief.

## Model setup and next milestone

Both model routes can be configured in **Settings → AI connections**, and the owner password can be changed in **Settings → Security**. See [model connections](docs/MODEL_CONNECTIONS.md) for private storage and the optional native worker. No keys are needed to refresh the public charts. [Market and analysis verification](docs/MARKET_ANALYSIS_VERIFICATION.md) records the current checks and limits.

Paper setup is in **Settings → Trading**, with the workflow under **Paper trading**. Create portfolios and nested capital pots, save a strategy version, then deploy that exact version to an account/allocation. Creating a new version leaves existing deployments unchanged. Deployment pause/resume records a reason and permits explicit closes of existing positions. See [strategy verification](docs/STRATEGY_VERIFICATION.md), [capital verification](docs/CAPITAL_VERIFICATION.md) and [paper verification](docs/PAPER_VERIFICATION.md). OpenClaw reviews use a linked successful original native report, a bounded queue and separate immutable decisions. Cloud/local routing and review budgets are in AI connections. See [governance verification](docs/GOVERNANCE_VERIFICATION.md). Saving a connection does not call a model.

Settings → Scanners saves named scanner profiles without requiring a strategy or paper account; strategy links are optional. Scan now and opt-in scheduled scanning screen supported closed crypto, Forex and stock/ETF candles without AI. Profiles can combine long breakout, short breakdown and trend pullback setups. A separate opt-in coordinator automatically admits qualified published candidates to Tauric research within the saved allowance; see [automatic scanners](docs/AUTOMATIC_SCANNERS.md). A downstream Candidate Gate and configurable ranking suppress duplicate, active and weak setups before research. See [candidate discovery architecture](docs/SCANNER_ARCHITECTURE.md) and [independent scanners](docs/INDEPENDENT_SCANNERS.md). The workspace Scanner panel preserves candidate evidence, watch/ignore history, chart links and explicit daily research requests with deduplication, expiry and admission allowances. See [scanner verification](docs/SCANNER_VERIFICATION.md). Paper trading now offers saved monitoring schedules for executable deployments; background checks persist rule results and attributed exit signals without submitting trades. See [scheduled monitoring](docs/STRATEGY_SCHEDULING_VERIFICATION.md). Opt-in autonomous paper dispatch now passes recovery, attributed exit and pause/resume checks with explicit AI/candle fixtures; see [autonomous paper verification](docs/AUTONOMOUS_PAPER.md). A real native BUY-to-automatic-exit run remains an acceptance gate. Next, extend scanner families, instrument/time contexts and routing capabilities. The full brief remains the target; intermediate milestones do not reduce its scope.

Broker-connected demo controls are available under **Paper trading → Broker-connected demo trading**. Both authorized demo routes are enabled with no additional owner caps. Real one-unit distant limit orders were accepted and cancelled on each broker, with zero fills and unchanged existing positions. See [demo broker execution](docs/BROKER_DEMO_EXECUTION.md). Attributed position journals, protective-child tracking and manual partial/final closes pass offline broker and browser recovery checks. An exact demo setup preview and separate native setup assessment now preserve the strategy version, executable signal, broker quote, requested quantity and original daily reports. This assessment grants no trading authority. A separate bound OpenClaw review now permits one exact demo entry, with optional automatic delivery, fresh submission checks and durable recovery. Its end-to-end broker/model evidence is currently offline. Scheduled broker routes, connected approval/fill-to-close validation and the full release gates remain pending.

Do not install or enable real-money trading from this repository at its current stage.
