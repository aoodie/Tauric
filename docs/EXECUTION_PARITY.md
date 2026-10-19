# Execution evidence and improvement sequence

Recorded 18 October 2026. Source implementation, test results and production acceptance are separate states. The existing [ownership mandate](OPENCLAW_PROJECT_OWNERSHIP.md) and all financial gates remain authoritative.

## Protected replay

Research experiments can explicitly select a broker-protection candle approximation for a saved long breakout on FX, stocks or ETFs. Existing replay requests keep their closed-bar/next-open behavior. Previously saved results and qualification protocols are never rewritten.

The optional `parameters.broker_protection` object requires a positive `price_step` and accepts `entry_delay_seconds` from 0 to 86,400. The implementation freezes the stop and target from the signal-time reference ask, rounds them in the same directions as the broker setup, and never reprices them after an entry delay or slipped fill. The historical closing ask is a proxy for a pre-submission quote, not a recorded broker order quote. Equity/ETF quotes use the declared spread assumption.

An entry fills at the first observed candle open at or after the assumed delay. It expires at the saved strategy signal lifetime. The engine does not invent an interior entry price or replace a pending breakout with an SMA signal. Median, nearest-rank 95th percentile and maximum signal-to-fill times describe this simulated observation grid, not measured AI or network latency.

Protection is modeled from the entry open on the assumed fully filled quantity. A known opening gap takes precedence over a queued channel exit. Stop gaps fill from the executable open with adverse slippage; take-profit limits cannot fill below their limit before fees. If an intrabar range touches both levels, the scenario chooses the stop and records the ambiguity. Intrabar exits are stamped at candle close because their actual time and path are unavailable. Financing uses that approximate holding period.

Forex requires historical bid and ask candles, their declared price-side context and zero added spread. A stop reference is already an executable bid threshold and is not charged another FX spread. Stock and ETF candles do not reconstruct quote eligibility or limit-touch liquidity.

Results expose `execution_evidence`, a distinct `broker_protected_ohlc_long_v1.0.0` engine version and frozen assumptions/source provenance. `broker_parity_verified`, `partial_fills_modeled` and `intrabar_path_observed` remain false. Full fills and immediate protection activation are assumptions. This is research evidence, not tick reconstruction, a connected broker acceptance result, profitable-strategy evidence or trading authority.

Saved protected results, API retries/exports, legacy replay semantics, bid-only FX stops, opening gaps, same-bar protection, ambiguous bars, delay/expiry and cash reconciliation have dedicated regressions. The built UI exposes the mode, assumptions and evidence. Hosted checks require the broker-route, project-task, Research Lab and protected-replay browser workflows to execute and pass; a skipped workflow fails the check.

## Remaining work and acceptance

| Work | Required evidence |
| --- | --- |
| Connected execution measurements | Actual per-stage queue/model/review/submission/fill timestamps; latency distributions, signal expiry, partial-fill protection and measured slippage, using existing authorized workflows |
| Execution-sensitive research | Finer observed bid/ask paths, measured costs and broker-specific fill/activation behavior; quantify unresolved ambiguity instead of claiming parity |
| Autonomous ownership | A fresh independently verified engineering-to-release cycle; preserve explicit pauses on existing acceptance goals and record any operator assistance |
| Strategy research | Registered hypotheses and attempted variants, unused holdouts and realistic costs; extend existing chronological purging with justified validation methods before selecting strategies |
| Infrastructure | Measured memory, database contention and workload/storage growth before choosing consolidation or columnar storage |
| Two-clock design | An explicit, revocable and expiring authorization contract with deterministic reservations; retain current per-signal authority until separately authorized changes pass acceptance |

Routine tested releases remain covered by the standing mandate. A mandatory owner merge for every ordinary release would add an approval requirement that the mandate does not contain. New financial authority, live activation and risk-policy changes still require their existing owner controls.

## Legacy IONOS installation on 19 October 2026

Release `0a11da8d26bec3c5f5377544a29cb4199ded6478` passed [the complete release workflow](https://github.com/aoodie/Tauric/actions/runs/39214835389) and was independently verified on the legacy IONOS server at 07:52 UTC. All 16 services were healthy; 163 loaded Python/instruction files and the OpenClaw plugin/saved instructions matched the published source. The actual installation backup was restored in isolation before migration. Protected authority/settings tables, all 15,996 historical readiness rows present at maintenance time, private configuration and original execution receipts were preserved. No financial settings, routes or order authority were changed. This is not evidence of an Oracle upgrade.

Hosted validation executed 1,177 backend tests, 56 deployment/tooling tests, four mandatory browser workflows and two real Qlib/LEAN engine tests. Qlib and LEAN use a separate Linux CI lane and disposable synthetic fixtures. This verifies the research runners; it does not install those heavy runtimes on the production VPS or establish a profitable strategy. LEAN now runs with the caller's user/group and a writable job home while retaining its network and capability restrictions.

The fixed release bridge was also reconciled with the independently verified previous running source before this upgrade. A fresh project goal executed isolated commands and existing replay regressions but did not complete its timing feature, new tests, release or asynchronous research criteria. It required operator review, used its 24 wakes and escalated on invalid effect-key arguments. The validation constraints were not relaxed. The owner subsequently directed Oracle-only consolidation; this legacy goal is paused with its original errors and evidence retained. Runtime ownership acceptance, connected broker evidence and strategy qualification remain open.

## Oracle-only direction

The owner selected Oracle as the sole deployment target on 19 October. The canonical workstation is [tauric.aoodie.xyz](https://tauric.aoodie.xyz). Fresh checks found source `d40048849e6cb4c226447fa5311aeaea8092c524`, 12 healthy services, trusted HTTPS and working owner authentication. Financial execution services remain disabled. Legacy installation evidence must not be presented as Oracle acceptance, and legacy data must not overwrite Oracle's active settings or ledgers.

The checked upgrade must retain the exact previously running service set, including during preparation recovery and an engineering-busy handoff. Explicit service selection with `--no-deps` prevents the enabled Compose profile inventory from activating previously disabled workers. The fixed host tools support Oracle's Python 3.10 runtime, with a separate mandatory native-host CI lane. Oracle installation and legacy retirement require their own backup/restore and final verification records.
