# Connected paper workflow — real approval, entry, close and journal

Recorded 3 October 2026 against built development 0.9.0 Linux arm64 services. **The real native analysis → bound OpenClaw approval → deterministic plan → paper entry → explicit close → reconciled journal path passed in an isolated disposable environment.** API-restart persistence and repeated request handling also passed.

One fresh native BTC analysis and one OpenClaw review used the saved external-local `ag/gemini-3.8-flash-high` connection. All nine original native report fields were present; governance remained separate and original execution authority remained false. There was no model-workflow retry, owner override or replacement of the fresh report with earlier evidence.

The fresh native final decision was **Hold**, describing a healthy structural trend while rejecting aggressive additions and defining conditional upgrade/invalidation triggers. This was not a native Buy recommendation. OpenClaw independently allowed the bounded disposable starter proposal under its saved strategy and risk policy, citing the daily directional report, fresh Binance observation, explicit simulation stop and allocation limits. This result verifies the connected mechanics; it does not establish that the model's interpretation of every discretionary strategy is correct or that the research facts are accurate.

## Positive-path evidence

| Check | Observed result |
|---|---|
| Native job | `4eb716a5-0fb0-4373-b2d1-9f428c3deaed`, succeeded |
| Original artifact | `fb80714c-32fe-4911-ac7a-3c38880e4972` |
| Original payload SHA-256 | `a96e556ae792ec89c0fa3537b1f192194bb8fbb1c73c519e995bd91e25f38842` |
| Report completeness and separation | Nine nonempty report fields; original governance absent, execution authority false |
| Real bound review | `8d4a9656-183c-43d8-a2a5-ba131fb841a2`, succeeded; context-tool receipt recorded |
| Context SHA-256 | `ea88b1ae629251c8c819053ff42a9c86f93781328742bd4702d53e1039be9ca0` |
| Separate decision | `206e25bd-4ade-4dcc-b9c7-6c3ad59feba1`, allow |
| Proposal / plan | `8f63ca0b-1c2d-40c4-9c21-41d29a561869` / `5d3e4a4b-86a7-4377-95c1-e2e4d270dc3c` |
| Entry / close | `0e2cf7b9-a124-4e57-b985-cf64dec9cc81` / `167c619f-a126-407d-a265-f528d608f96d` |
| Simulated quantity | 0.00007 BTC for both fills |
| Entry / exit prices | 84829.833720000000 / 84745.046280000000 USDT, including configured adverse slippage |
| Independent journal reconstruction | Exactly two fills; checksums, original-report links, strategy/allocation lineage, cash deltas, fees and realized P&L verified |
| Final cash / realized P&L | 9999.98219463760000000000 / -0.01780536240000000000 USDT |
| Ledger | Reconciled; no open position |
| Duplicate native/review/plan/fill/close requests | Each returned its original record; no additional workflow/fill |
| Original preservation | Payload/checksum unchanged through execution |
| API restart | Same journal reconstructed and records readable with the existing owner session |
| Review reported usage | 26,209 prompt; 17,742 completion; 43,951 total tokens |
| Broker order calls / primary paper changes | Zero / zero; read-only fingerprints matched for primary accounts, deployments, trading records, capital, strategy and control versions |
| Retained evidence SHA-256 | `5e654c24216651ec4faa2fbcf773c850f8bf44495c64255970946924da528489` |

The disposable account used fictional starting cash of 10,000 USDT, a 250 USDT allocation, one position maximum, account and strategy per-trade risk limits of 0.1%, aggregate account risk of 0.5%, maximum account exposure of 5%, 10 bps fees and 5 bps adverse slippage. The stop reference was explicitly set 3.75% below a fresh Binance USDT observation as a simulation assumption. It was not converted from the native USD report. The close was an explicit verification action; stop/target monitoring and autonomous dispatch are not certified by this run. This immediate round trip is not a profitability test.

## Evidence retention, cleanup and remaining scope

Private ignored evidence remains in `artifacts/real-paper-native-report.json`, `artifacts/real-paper-workflow-results.json` and `artifacts/real-paper-workflow-evidence.json`. Image digests and primary-paper before/after fingerprints are retained separately. Earlier evidence was archived privately before these files were replaced. Temporary containers, native/OpenClaw volumes, database, owner credentials and encryption key were removed. Disposable resource IDs above are evidence references, not resources in the primary owner's application.

Usage is the Gateway's report, not verified monetary cost or exactly-once billing. Native graph provider usage is not aggregated here. Cloud, broader instruments/time contexts, point-in-time evidence, general provider certification, scheduled evaluation/continuous exit monitoring, autonomous/live execution, broker adapters and Linux installation/recovery/load gates remain pending. Scheduled monitoring was pending at this check; development 0.10.0 now records its separate verification in [schedule verification](STRATEGY_SCHEDULING_VERIFICATION.md).

See [reproduction instructions](DEVELOPMENT.md), [executable strategies](EXECUTABLE_STRATEGIES.md) and the [earlier real rejection check](REAL_GOVERNANCE_VERIFICATION.md).

## Earlier fresh-analysis rejection check

Recorded 3 October 2026 against the built development 0.8.0 Linux arm64 services. One fresh native analysis and one real bound OpenClaw review succeeded in a disposable paper environment. The positive acceptance gate was still open at that point; the later development 0.9.0 check above closes it for this isolated daily BTC paper path.

The new native report rated BTC **Underweight** and recommended reducing exposure. The verification strategy required supportive current daily research for a long entry. OpenClaw independently rejected the buy proposal for that conflict; planning returned HTTP 409 and created no plan or fill. This is correct rejection behavior, not a successful positive trading workflow. The earlier bullish report was not substituted for the new evidence, and no retry or owner override was used to obtain approval.

## Scope and evidence

The saved external-local model `ag/gemini-3.8-flash-high` served both native roles and the real pinned OpenClaw Gateway/plugin/governance worker. Credentials were re-encrypted under a temporary key without printing them. The unmodified pinned TradingAgents 0.5.2 graph produced all nine original report fields. Actual public Binance BTCUSDT observations supplied separate paper pricing; the native daily USD context retained its original limitations.

The disposable account had fictional starting cash of 10,000 USDT, one position maximum, 0.1% account per-trade risk, 0.5% aggregate risk, 5% maximum exposure, 10 bps fees and 5 bps adverse slippage. Its versioned daily swing starter strategy and shared allocation capped capital at 250 USDT, with a 0.1% strategy risk limit. The USDT stop reference was explicitly defined as 3.75% below the observed Binance price, as a simulation assumption; it was not extracted or converted from native USD prose. Stops remain sizing references, not monitored protective orders. None of these settings were installed in the real owner's account.

| Check | Observed result |
|---|---|
| Native job | `fffe4df6-fdd1-48f8-ba41-829d42059980`, succeeded in 243 seconds |
| Original artifact | `1b97dd15-7300-471e-9cb6-7443e15d42a3` |
| Original payload SHA-256 | `6e0ac074cd3e7d2eae5e882f194092577aee7debe67e11c0d7d6afd13d1f3890` |
| Native report fields | All nine nonempty; original execution authority false, governance absent |
| Real review | `aaa20304-a309-4053-b2a8-7d913bb0ccda`, succeeded in 16 seconds |
| Bound context | Tool receipt recorded; strict identifiers/checksum/outcome validated |
| Context SHA-256 | `6908279a36b3cff72474c07366c941bfd67dd0d0616afbbd1e2476fbfc70a03a` |
| Decision | `cd12931e-7559-4dbd-8801-e9bde98b7077`, reject |
| Decision reason | Current research is Underweight and recommends selling/trimming; incompatible with the strategy's supportive-research buy requirement |
| Duplicate native/review admission | Same request returned the original job; no additional run/review |
| Planning after rejection | HTTP 409; zero plans and fills |
| Ledger | Reconciled, no open positions |
| Original preservation | Payload/checksum unchanged before and after attempted planning |
| API restart | Saved decision and reconciled ledger readable afterward using the existing owner session |
| Review reported usage | 27,017 prompt; 2,943 completion; 29,960 total tokens |
| Broker order calls / real-owner paper changes | Zero / zero |

Usage is the Gateway's report, not a verified monetary cost or exactly-once billing claim. Native graph provider usage is not aggregated in this record.

## Improved verification path

The verification helpers now support either a selected/current preserved report or a fresh isolated native run. An allowed result is checked through plan, entry, explicit close and independent Decimal reconstruction of cash/P&L from checksum-verified journal records. The verifier checks report/decision/strategy/allocation lineage, exactly two fills, repeated plan/fill/close requests, unchanged original research and persistence after API restart. Reject/defer cannot enter that branch or be labelled positive success.

Eighteen targeted governance tests passed. The cloud/local fixture-positive cases now exercise closes, duplicate fills/closes and independent journal reconstruction; altered cash and mismatched original-report lineage fail verification. These fixture checks make no real model calls and do not certify an affirmative real-model trade.

This earlier private evidence was archived before the latest run replaced the standard artifact filenames. The archived evidence contains the original report, captured review context, decision and paper records. Temporary containers, native/OpenClaw volumes, database, owner credentials and model encryption key were removed. The disposable resource IDs above are evidence references, not resources in the real owner's application. A redacted verification audit is retained in the primary application.

See [reproduction instructions](DEVELOPMENT.md) and the [earlier real rejection check](REAL_GOVERNANCE_VERIFICATION.md). The later check above verifies the real positive branch. Live brokers, autonomous execution and server installation/recovery gates remain separate pending work.

