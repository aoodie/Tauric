# OpenClaw governance and actual capability audit

Observed 9 October 2026, approximately 19:16–19:18 UTC. This is a snapshot of the deployed system, not a statement about subsequent releases.

## Verdict

Open research is enabled for Forex, stocks and commodities, including markets without native daily reports. Exact prices and historical candles depend on provider coverage, retention and account entitlement. This does not make every product executable.

Automatic trading is currently **disabled**. Neither broker has an enabled execution policy, no automatic broker routes are saved, and no internal paper-autonomy policies are saved. OpenClaw is not currently equivalent to a general Codex engineering agent: the mandate authorizes that role, but the deployed runtime lacks its required engineering tools and durable goal lifecycle.

## What is available now

| Capability | Deployed implementation | Current saved state |
| --- | --- | --- |
| Open source research across Forex, stocks and commodities | Search, original-page reading, instrument discovery and price history; registration and native daily support are unnecessary | Enabled; owner chat and model route healthy |
| Built-in daily native reports | Registered supported crypto, stocks/ETFs and provider-verified currency pairs | Chat allowance is 3 native research requests per UTC day; 0 used at observation |
| Scheduled portfolio research | Separate Tauric service with saved policy and schedule, rather than general OpenClaw cron | No policies saved |
| Automatic broker entries for supported Forex | OANDA route with exact strategy/instrument, native setup assessment, independent OpenClaw review and deterministic execution checks | Practice connection saved; execution disabled; no routes |
| Automatic broker entries for supported stocks/ETFs | Alpaca route with the same governed sequence | Paper connection saved; execution disabled; no routes |
| Automatic commodity entries | No commodity route in the current broker strategy gate | Unavailable |
| Real-money trading | Separate live-policy activation and exact-strategy prospective validation gates exist | Both brokers have `live_enabled=false`; 0 forward protocols and 0 reports |
| Internal paper simulation | Guarded project workflow tools; separate autonomous paper worker | No autonomy policies saved; Forex/stock automatic entry restrictions in this simulator differ from broker demo routes |
| General engineering ownership | Source reads, research notes and immutable patch workspaces | No shell, Python execution, Git, test runner or release tool available to OpenClaw |
| General unattended continuation | Intended by the ownership mandate | Gateway cron disabled; heartbeat `0m`; no general goal/task registry identified |

The native chat request allowance above does not impose a native-report prerequisite on the separate open research tool. No owner-wide shared AI admission cap is saved. Existing provider and resource limits still apply.

Saved broker connections are not proof of current account connectivity or tradability. This audit did not contact brokers. Empty `entry_blockers` arrays in readiness state are not evidence of eligibility: the live-only checks are skipped when the policy is disabled or the environment is practice/paper.

## Findings

### P1 — Autonomous project ownership is authorized but not operational

The ownership mandate explicitly calls for general engineering, durable goals, restart/resume, bounded recovery and verified completion. The actual Gateway has cron disabled, a `0m` heartbeat, shell commands disabled, code mode disabled and only Tauric-specific tools allowed. Its governor agents can read review context; they cannot place orders or repair the project themselves. The chat agent cannot delegate to general executors through its current allowlist.

Existing Tauric workers do have durable service-specific jobs. Their presence does not supply a general supervisor that wakes OpenClaw, follows asynchronous work, resumes parent goals or verifies final outcomes. The database has no goal/task-named tables, which corroborates the documented implementation gap but is not the sole basis for this finding. `stage_upgrade` checks source identity, patch context and Python syntax; it explicitly records `tests_executed=false` and `runtime_code_changed=false`.

Required implementation: a durable goal/task supervisor with permissions, dependencies, stable effect identities, lease/recovery limits, pause/cancel behavior and authoritative completion checks; an isolated engineering executor for files, shell, Python, Git and actual tests; and a governed release/recovery interface. Production financial credentials and approval-writing authority must remain outside that engineering executor.

### P1 — Deployed instructions advertise operations absent from the deployed API

The deployed chat instruction file has SHA-256 `0b262852417af8fbb2c3ea88df5b8d613172d09033bcc422cbe5b4c65f07577e`, matching the current local instructions. It describes `retrieve_history`, `session_research`, `quant_experiment` and broker actions such as `save_broker_route` and `submit_broker_entry`.

The deployed assistant accepts only 17 operations. Those advanced research and broker operations are absent from both its accepted operation schema and dispatch contracts. `assistant_brokers.py` is absent from the deployed API. Broker-readiness resources described in the instructions are also absent from its project-read resource enum. The instructions additionally contain an older prohibition on chat broker submissions alongside the newer broker-tool paragraph.

This mismatch can cause rejected tool calls or misleading claims about what OpenClaw can perform. Local source containing those tools is not evidence of deployment. Align instructions, plugin schemas and installed server implementations in a verified release, and make fresh server capabilities authoritative. This finding does not establish a financial-control bypass: absent operations are rejected by the deployed API.

### P2 — Research coverage is broader than executable asset coverage

The deployed broker-route gate matches the local source checksum. It accepts `fx` through OANDA and `equity`/`etf` through Alpaca, and still requires native support. The native currency check excludes XAU, XAG, XPT and XPD. There is no commodity branch in the route gate. Current strategy routes cover the implemented long-position workflow, not arbitrary trading strategies or all broker products.

Removing the research-screen restriction did not remove this financial qualification path. Supporting a commodity or another product requires its exact instrument and contract identity, data, executable strategy, sizing, protection, reconciliation and qualification path to be implemented and tested. A general research answer cannot replace that evidence. “Any asset” permission alone cannot create the missing implementation.

### P2 — Healthy workers do not mean automatic trading is enabled

All 15 Tauric containers were healthy, and the native, governance, broker, valuation and forward-validation workers had recent heartbeats. However, there are no broker execution policies, broker routes, broker order intents, broker review jobs, forward protocols or forward reports in the owner's saved state. No automatic paper policies or scheduled portfolio research policies are saved either.

This is a configured-but-inactive platform, not an operating universal autonomous trader. The absence of Tauric order intents says nothing about orders placed outside Tauric; this audit did not inspect broker account histories.

## Intended authority boundary

For an eligible enabled route, Tauric discovers a strategy signal, obtains a native assessment, requests OpenClaw's bound independent review, and uses its deterministic broker service to submit and reconcile an eligible intent. OpenClaw can reject, defer or reduce approved sizing; it cannot fabricate an approval, increase the approved ceiling, bypass a rejection or send an arbitrary direct broker order.

The existing standing project mandate already authorizes routine development, research within saved allowances, tested releases and service recovery. The missing general-agent capabilities are implementation gaps. Live-account activation, financial-policy changes and changes to explicit owner pauses remain separately controlled.

## Evidence and limits

This audit used read-only database transactions, selected non-secret Gateway configuration, installed Python operation schemas, source checksums and container release identities. It did not change settings, enable trading, submit research jobs, place orders, run a strategy or perform a deployment. No behavioral trading or recovery acceptance test was attempted.

- [Deployed database and API capability snapshot](/Users/aoodie/Documents/Codex/Tauric/artifacts/openclaw-governance-runtime-20261009.json)
- [Deployed Gateway tool and scheduling snapshot](/Users/aoodie/Documents/Codex/Tauric/artifacts/openclaw-governance-gateway-20261009.json)
- [Container health and release identities](/Users/aoodie/Documents/Codex/Tauric/artifacts/openclaw-governance-deployment-20261009.json)
- [Local versus base-release source comparison](/Users/aoodie/Documents/Codex/Tauric/artifacts/openclaw-governance-source-comparison-20261009.json)
- [Standing project ownership mandate](/Users/aoodie/Documents/Codex/Tauric/docs/OPENCLAW_PROJECT_OWNERSHIP.md)

API and worker images still report base revision `18b3d34473b666cfa83df6679357481bd4c504bb`; the API, chat worker and Gateway have the previously deployed open-research supplement. The web service has the separately verified research-screen supplement. File checksums, not base-image labels alone, established the capabilities reviewed here. Newer local broker and research code must not be described as installed on the strength of source presence alone.
