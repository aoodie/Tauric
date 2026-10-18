# Trading and operational acceptance

Owner authorized this work on 8 October 2026. The first milestone is reliable governed demo trading on IONOS. Strategy promotion and completion of the agreed full platform remain separate milestones.

## Baseline

Verified starting source: `0e30a3bdfb6206544cb6bff242c8e0ac5b223eaf`. Its [hosted release](https://github.com/aoodie/Tauric/actions/runs/37677136199) passed 968 backend tests, 11 deployment checks, frontend/OpenClaw checks, four amd64/arm64 image publications and installation/full restoration; 20 browser tests were deselected. Saved IONOS evidence records trusted public-IP HTTPS, reboot recovery, restored backups and encrypted off-host backups. A fresh 8 October read-only SSH check found all 15 services healthy using the verified project host key.

The target server initially had no saved strategy, research portfolio, automatic route or prospective protocol. Its state is separate from the workstation's enabled demo routes. A new target-host research context remains halted and grants no broker execution authority.

The progress-reporting candidate is assembled over published assistant upgrade `363fd9b7c1acbf443d361330db72f8c56c39d49c`, preserving that upgrade without overwriting the shared release branch. A separate branch carries the monitoring changes. Bind all candidate sources and hosted image digests to one tested commit before deployment and prospective registration.

Current deployed monitoring source: `18b3d34473b666cfa83df6679357481bd4c504bb`, preserving assistant source `363fd9b`. Hosted checks passed 1,004 backend tests and 11 deployment controls; all four image jobs and clean installation/restoration passed. All 15 target services and seven progress lanes are healthy on schema 0033. The failed research result still blocks prospective registration. A failed post-migration startup was recovered in isolated copies, and a fresh current-state backup passed full restoration. See [the completed validation record](VALIDATION_RESULT_2026-10-08.md) for scope and the pending signed-in check.

## Work order

| Step | Work | Complete when |
|---|---|---|
| 1 | Correct status documents and pin evidence | Source, images, schema and recorded gates agree |
| 2 | Evaluate one unchanged EUR/USD breakout | Frozen dataset/split/costs/criteria; numerical prerequisites pass, followed by applicable exact native verdict |
| 3 | Deploy monitoring and preregister prospective validation | Tested reporting, dedicated empty account, passing current research report and frozen execution source |
| 4 | Observe automatic trading | Qualifying candle → native approval → OpenClaw permission → one fill → confirmed protection → attributed exit → reconciled journal |
| 5 | Establish sustained operation/recovery | Representative 24-hour workload; isolated failed migrated-upgrade recovery; scheduled encrypted backups/retention; certificate-renewal evidence |
| 6 | Evaluate contribution and finish release scope | Frozen prospective criteria, matched shadow comparisons, critical browser CI and remaining broker/platform acceptance |

## First frozen research trial

Keep the existing rules: 15-minute long breakout above the previous 20 highs, at least 1.5× tick activity, prior-low stop, 2R target, 10-bar channel exit and 900-second validity. Collect exactly 5,000 complete OANDA midpoint/bid/ask bars ending before `2026-10-02T04:15:00Z`, the previously observed diagnostic window. Freeze 3,250 training bars, 20 embargo bars and 1,730 holdout bars before evaluating results.

Require at least 1,500 holdout bars, 20 completed holdout trades, nonnegative return at baseline and stressed costs, and drawdown at most 10%. These engineering screening criteria do not prove an edge. Preserve failures; do not relax criteria or tune parameters on this holdout.

Historical bid/ask already includes spread. Preserve explicit conservative research assumptions: zero synthetic spread, 5bp baseline slippage and 1bp financing debit per rollover day; stress at 5bp fees and 15bp slippage. These are assumptions, not historical broker measurements. Quote-currency replay does not establish broker-home currency conversions, actual costs, latency or partial fills. FX breakout replay exits use closed-bar rules; broker protection can execute intrabar. The corrected SMA stop diagnostic does not establish breakout execution parity.

`scripts/acceptance_research.py` uses broker GET requests, stable request IDs and immutable research records. It enables no routes, sends no orders and makes no model calls. Request exact native assessment only after numerical prerequisites pass. A failed trial blocks this strategy's prospective promotion milestone.

Provider references: [OANDA candles](https://developer.oanda.com/rest-live-v20/instrument-ep/), [financing/commission fields](https://developer.oanda.com/rest-live-v20/primitives-df/), [order triggers](https://developer.oanda.com/rest-live-v20/order-df/).

### Recorded result — 8 October 2026

The unchanged baseline failed the frozen screening criteria. Training produced 41 closed trades and a −1.9183% return. The separate holdout produced 16 closed trades (required 20), zero winning trades and a −1.4614% return (required ≥0), with 1.4902% sampled drawdown. Higher-cost replay returned −3.7942%. Coverage passed at 1,730 bars. No exact-version native assessment was requested because the numerical prerequisites failed. No prospective protocol or execution route was created.

Protocol `29781c2e-ee38-4897-9d1f-7c55f826be0c` checksum `6048b556234f835981c588977e19570c00e49614c54b4ddc4187ddfa959a18e9`; evaluation checksum `91fc974738452c5bbd4a6c23fa7516c061010c95fb8309a7d6b00e69fe9a3f76`; failed report `024ae0c0-d882-43d2-af39-b3921ec03b43`. These immutable records remain on the target host. The local evidence export is `artifacts/acceptance-eurusd-research-20261008.json`.

Step 2 is evaluated but failed; prospective registration and the connected-cycle acceptance remain blocked for this strategy. Monitoring deployment can proceed independently. A later strategy revision requires a stated new hypothesis, a new registered trial and unused holdout evidence; these failed results must remain visible.

## Operational evidence and prospective registration

Migration `0033_worker_progress` adds bounded mutable per-lane counters, invocation IDs, start/finish times, idle successes, completed work, failures and sanitized error categories. Interrupted/stale lanes are visible. Telemetry failures never block protection or reconciliation. Owner-authenticated `/api/v1/trading-readiness/operations` exposes progress, oldest queued native/review work and unresolved orders/closes. Trading displays a compact expandable view. Idle success is not evidence of a complete trade. Existing immutable job/order receipts preserve latency and reported model usage; missing cost evidence stays unavailable.

The execution signature includes the progress implementation and both evidence workers. Deploy before preregistration; later changes invalidate the signature. After passing current research qualification, confirm an empty, dedicated practice account and no pending orders. Ensure another workstation route/operator cannot trade that account during validation. Freeze the existing defaults: 14 days, 20 completed trades, positive net NAV change, drawdown at most 10%, signal-to-fill latency at most 900 seconds and adverse slippage at most 25bp. Keep the intended route's 1% risk ceiling; unused higher risk does not qualify. Continuous marks, clean activity and complete broker costs remain required.

Evaluate rules-only/native-only alternatives in shadow mode on the same eligible signals. Actual broker orders retain mandatory native Tauric and OpenClaw approval. Label simulated outcomes and missing coverage explicitly.

IBKR, Binance spot/futures and outstanding platform features remain full-release scope. Only the interface additions in `POST_LAUNCH_TODO.md` are explicitly post-launch. Live activation retains its separate evidence and owner-account gates.
