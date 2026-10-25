# Audit corrections — 25 October 2026

These source changes address the reproducible findings in the repeated audit. They do not activate broker execution, alter saved owner limits, create qualification evidence, or change an existing strategy's approved stop and target prices.

## Execution and accounting

- Automatic Alpaca setup requests now use GTC, matching replacement protection. Shares remain whole units. GTC preserves order lifetime across sessions; bracket orders still do not execute outside regular hours, and stop prices do not guarantee fill prices. See [Alpaca order documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca).
- An authenticated owner can restore an entry's original stop/target protection without fabricating a close request. The position must have covered holdings, no unresolved close, and no active or uncertain protective orders. A committed repair identity is reconciled after an uncertain response and never submitted twice. The position journal and existing owner-bound broker tool expose this action.
- Alpaca coverage compares the broker position with the sum of actual remaining quantities across the owner's active entries for that symbol, side and account. Peer fills are reconciled from broker evidence, with integrity checks and a bounded inventory. Unknown or excessive attribution fails closed; it is not assigned to an arbitrary entry. The journal displays aggregate attribution separately from the entry quantity.
- Credential revisions remain part of protocol identity, but drawdown continuity follows the same provider, environment, account fingerprint and currency. Rotating credentials does not start a new high-water mark. A different account does not inherit another account's drawdown. Historical records remain unchanged.

## Valuation and market sessions

Fresh account NAV observed during a verified market closure can maintain prospective coverage, while unavailable position marks and exposure values remain visibly incomplete. This does not make those marks eligible for a new live entry. Alpaca closures require its fresh clock; OANDA closures use the instrument's verified product type and known regular session rules. Unexpected quote failures, unknown sessions, stale NAV, attribution errors and worker outages still fail coverage. The five-minute coverage requirement is unchanged.

Known OANDA USD metals, energy products and US-session index/bond products use their published regular weekly and maintenance sessions, including New York DST. Other product schedules and holiday exceptions remain conservative rather than being inferred from Forex hours. The exact supported symbol sets and source are in `backend/tauric/market_calendars.py`. See [OANDA trading hours](https://www.oanda.com/uk-en/trading/hours-of-operation/).

## Autonomy, access and presentation

- A finished wake that leaves its task running consumes the shared recovery allowance even if chat transport succeeded. Retries back off and exhaust their existing budget instead of spending the wake allowance on repeated successful but non-transitioning turns.
- The release bridge targets `main`.
- Caddy overwrites a dedicated client-address header. The API accepts it only when the socket peer resolves to an explicitly configured proxy host. Untrusted peers, malformed addresses and arbitrary forwarded headers cannot select another throttle key. Login counters remain persistent in PostgreSQL.
- UI policy changes preserve explicitly saved limits. Missing policy settings no longer silently become an unlimited policy; a new account requires the owner's visible settings choice before enabling entries. Existing explicit broker-only policies remain supported.
- Generated OpenClaw credentials and runtime configuration are written under `/tmp/tauric-openclaw`, which is on the container's existing tmpfs, with private permissions and atomic config replacement. Startup removes only the three obsolete generated credential/config files from persistent state. Existing backup archives and the private disaster-recovery secrets bundle are not rewritten.
- The sidebar reads live trading status instead of hardcoding “Off”.
- Broker action activity validates the broker service's checksum format and follows the observed protection state, including expiry after a successful repair.

## Findings that did not justify a behavioural change

The discovery confirmation path already applies the configured minimum closed-trade count and baseline/stressed-cost checks. A one-trade confirmation was rejected with the default 20-trade requirement; no weaker replacement gate was added.

Fixed reviewed stops/targets, delayed fills and slippage are explicitly modeled in protected replay. Current submission checks reject quotes crossing those levels and enforce the captured risk budget. These corrections do not move an approved stop or target after review. Actual broker execution parity still requires prospective evidence.

## Verification and deployment boundary

Regression cases cover shared-symbol shortfalls, entry repair and lost responses, successful chat turns without state transitions, proxy spoofing and independent visitor throttles, product session gaps, closed-market NAV coverage, and drawdown continuity/account separation. A synthetic 14-day observation series spans two weekends; an injected six-minute outage still fails coverage.

Local validation completed:

- Full backend suite: 1,211 passed (browser and external research-runtime tests excluded).
- Additional repair-tool and activity verification: 16 passed, including three new cases added after the full run.
- Six built-frontend browser workflows passed with disposable offline brokers; the broker workflow restores protection, verifies coverage, and closes the attributed quantity.
- Host deployment tools: 68 passed. All three Caddy configurations validate, and the development Compose configuration validates.
- Frontend type checking, production build and formatting; backend/script lint; pinned OpenClaw plugin, runtime configuration and heartbeat checks passed.

These checks use synthetic broker fixtures and do not place orders at a connected brokerage. Hosted research-runtime checks remain part of the pull request workflow.

Oracle was independently inspected before implementation: the active release and running API image report `1b4522732ec3a1bed7a28d6f851191e73554c455`, whose Git tree matches main `6a3d815c0269e596d8e08d00e79574b52d89a088`. All 12 intended services were healthy, the financial execution services were stopped, and the production broker policy, route and intent tables were empty.

These changes alter the execution source fingerprint. The existing installer in `scripts/release_install.py` rejects that change without separate trusted qualification. That guard remains intact. Passing development tests is not deployment or strategy qualification, and this document does not claim that the corrections are installed on Oracle.
