# Server and installation readiness

**Current status — 8 October 2026:** the IONOS candidate is deployed and healthy, with hosted images, public HTTPS, reboot recovery and backup/restore evidence. Production acceptance remains incomplete: representative 24-hour workload, connected automatic demo trading, failed migrated-upgrade recovery and actual prospective strategy evidence remain open. The [acceptance plan](ACCEPTANCE_PLAN.md) is the current work order; older assessments below do not mean the server is still uninstalled.

**Deployment-package update, 5 October 2026:** the [GitHub/server candidate](GITHUB_DEPLOYMENT.md) is implemented. The isolated Linux arm64 container rehearsal verifies startup, owner/configuration persistence, service restarts and database/runtime restoration. GitHub-hosted publication, clean target Linux installation, HTTPS certificate operation, host reboot, representative 24-hour load and connected automatic broker approval-to-fill-to-exit remain open. See [current package evidence](SERVER_DEPLOYMENT_VERIFICATION.md). The full platform is still not certified for production installation.

The automatic OANDA practice/Alpaca paper routes are implemented, and an owner-authorized OANDA practice fill/protection/close check passed separately. These updates supersede the earlier scheduled-broker and filled-trade gaps below, while connected automatic model-to-fill validation remains open; see [automatic route evidence](BROKER_AUTOMATIC_ROUTES.md).

## Earlier platform assessment

**Current status: full platform not ready for server installation.** Development 0.11.0 runs locally in Linux arm64 containers. Real Binance charts, one native daily BTC analysis and one real bound OpenClaw rejection review are verified. Manual crypto spot screening, immutable candidates, research deduplication/expiry/admission allowances, strategy versions, shared allocations and owner-reviewed paper execution work. A fresh real bound approval-to-paper-entry/close path also passes journal reconstruction and API-restart persistence in an isolated daily BTC environment; see [connected paper verification](REAL_PAPER_WORKFLOW_VERIFICATION.md). Broader scanners, connected unattended autonomous paper verification, scheduled broker execution, connected filled-trade validation and supported Linux installer/recovery/load checks remain pending. See [scanner verification](SCANNER_VERIFICATION.md), [real governance verification](REAL_GOVERNANCE_VERIFICATION.md), [connected AI verification](CONNECTED_AI_VERIFICATION.md), [strategy verification](STRATEGY_VERIFICATION.md), [capital verification](CAPITAL_VERIFICATION.md) and [paper verification](PAPER_VERIFICATION.md). Saved web chat and bounded native daily research dispatch are verified against the connected model; owner-only Telegram/Discord configuration is prepared, with real delivery awaiting owner bot credentials. See [chat setup](OPENCLAW_CHAT.md). The actual IONOS host and its resource capacity have not been measured.

## Initial host assumptions

The owner identified IONOS VPS Linux M+. The UK listing reviewed on 2 October 2026 advertises 4 vCores, 4 GB RAM and 120 GB NVMe. Existing or regional packages can differ, and the actual server, installed distribution and free resources have not been inspected. [IONOS UK listing](https://www.ionos.co.uk/servers/vps).

M+ is a candidate for bounded development/testing with cloud AI or an external local model endpoint. A 16 GB application host is the initial engineering preference for the full stack, not a measured minimum or guarantee. Running local models on the M+ host is outside the agreed deployment approach.

Target Ubuntu 24.04 LTS or Debian 12 with Docker Engine and Compose v2 for the first compatibility tests. Preflight must check actual architecture, kernel/distribution, installed Docker/Compose, free RAM/storage, occupied ports, clock synchronization and outbound access. Never assume the package name confirms these facts. Support a distribution only after its deployment tests pass; do not reimage an existing server automatically.

## Deployment design

- Serve the built frontend and API through one ingress. Default initial testing to SSH/VPN/private access; support an owner-supplied hostname and HTTPS for public ingress.
- Publish no database, worker, OpenClaw Gateway or broker-gateway ports to the internet. Browser requests go through the platform; Gateway/operator tokens stay server-side.
- Store secrets outside source control and mount them privately. Validate missing credentials without printing them. Do not ask for credentials in chat; supply them on the server during setup.
- Use persistent PostgreSQL and runtime volumes, controlled log retention and explicit dataset retention. Begin M+ tests with one expensive job at a time; concurrency is a configurable resource limit, not a trading rule.
- Keep model inference external. Local endpoint connectivity requires a reachable private network/VPN path and verified authentication, tool calling and structured output.
- OANDA, IBKR, Binance and Alpaca require their own accounts, API access, product permissions and any market-data entitlements. A chosen broker name does not establish eligibility or availability for a specific account.
- IBKR requires reachable TWS/IB Gateway and an operational login/renewal procedure. Broker secrets and any gateway session volumes stay isolated from OpenClaw.

## What “ready to install” must mean

1. All agreed substantive platform features and all four broker adapters have working, documented paths; remaining external account prerequisites are listed explicitly. Installation and connected live validation have separate statuses.
2. A reproducible versioned release exists with locked dependencies, immutable image digests, a release manifest and documented supported hosts.
3. A clean supported Linux host can pass preflight, create the owner, start services, run migrations and reach application readiness using the supplied instructions.
4. An owner can connect data/model services, complete an actual Tauric → OpenClaw → paper execution → fill → journal workflow, and inspect configuration/evidence/strategy/account lineage.
5. Broker test/demo paths, unsupported capability handling, attribution, reconciliation, idempotency and unknown-submission recovery have evidence. Live connectivity and any authorized live tests are recorded per adapter/product/account; untested live behavior stays explicitly unverified.
6. Authentication, session/CSRF behavior, secret redaction, private service ingress, database permissions and immutable-record protection have been checked.
7. A 24-hour representative soak test exercises bounded analysis/scanning workloads, reconnects and periodic reconciliation. There are no unexplained duplicate submissions, ledger discrepancies, data gaps, out-of-memory kills or unbounded queue growth. Record actual resource and latency measurements; the owner's chosen cadence/universe determines the workload.
8. Service restart, host reboot, job interruption, provider outage and lost network scenarios preserve state and expose failures. Protected positions and halted deployments follow the documented shutdown/recovery policy.
9. A database/runtime backup is restored into a separate environment and its accounts, configuration, decisions and journal reconcile. Off-host backup storage and retention are documented.
10. Upgrade/rollback has been rehearsed with migration compatibility and a release-specific recovery procedure. The installation guide identifies any manual IBKR authentication or provider steps.

Until these checks exist, report the milestone actually achieved: architecture reviewed, development foundation tested, paper workflow verified, broker test path verified, installation package verified, or connected live behavior verified. Do not collapse those statuses into “production ready.”

## Installation deliverables

The final package will include an installation guide, preflight and setup commands, example configuration without secrets, Compose files, release/coverage manifests, backup/restore commands and troubleshooting steps. It will not contain real broker credentials or enabled live deployments. Autonomous trading starts only after the owner configures and enables the intended accounts and policies.


Local research now includes immutable retrospective datasets, costed SMA and pinned breakout replay, and [exact-version crypto research-comparison qualification](STRATEGY_QUALIFICATION.md). Read-only [FX/stock data adapters](MARKET_DATA_ADAPTERS.md) support calendar-aware charts and explicit-cost SMA replay; connected feeds remain unverified without credentials/entitlements. Analytics reconcile closed paper-trade account/strategy outcomes. Point-in-time AI/news replay, forward execution qualification, paper/live strategy promotion and marked/live analytics remain open.

Opt-in scheduled paper strategy monitoring is verified locally with immutable receipts and restart recovery; see [schedule verification](STRATEGY_SCHEDULING_VERIFICATION.md). The separate [autonomous paper workflow](AUTONOMOUS_PAPER.md) now passes synthetic native/governance, process restart, attributed exit, reconciliation and pause/resume checks. A connected unattended native BUY-to-automatic-exit run remains uncertified. These checks do not establish broker protective orders, long-running load capacity or installation readiness.


OANDA practice and Alpaca paper are connected with owner-authorized no additional trade caps. Zero-fill distant limit submission/cancellation is verified on both. Exact native setup reviews and a separate bound OpenClaw permission can feed one durable demo intent, with optional automatic delivery, one-shot recovery and fresh execution checks. Offline browser/database/transport tests cover fills, attributed closes and preservation of external holdings. Scheduled discovery of new broker entries, connected native/OpenClaw approval and filled-trade lifecycle validation remain pending; this is not full unattended or server-release certification. See [demo broker execution](BROKER_DEMO_EXECUTION.md). IBKR and Binance/crypto execution are deferred under the current owner priority.
