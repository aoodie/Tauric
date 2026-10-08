# Session research, LEAN and Qlib

Research → **Research Lab** shares saved, checksummed datasets across session reports, machine-learning studies and execution backtests. Session reports work with registered instruments that have valid saved data. Execution and machine-learning experiments currently support Forex currency pairs, stocks and ETFs.

Historical, current/intraday and forward reports are separate modes. Reports describe prices, activity, ranges and conditional scenarios. They preserve timestamped owner-supplied news/macro evidence. They are not the native daily analyst graph. Forward scenarios and model estimates never represent known future observations or calibrated probabilities. Evidence published or available after the selected cutoff is rejected. Provider revisions and declared evidence availability are not independently verified point-in-time data.

London, New York, Tokyo and US stock regular-session presets use IANA time zones and daylight-saving rules. Custom hours, weekdays and overnight windows are supported. Only whole signal bars inside the window are selected. Overnight sessions belong to their opening weekday. Prediction horizons count subsequent trading bars and may span sessions; daily bars cannot fit a narrow intraday session. Identical opening/closing times mean a full local day.

## Data

Capture existing chart snapshots, import your own historical data through Research experiments, or retrieve a bounded historical range through Research Lab. Retrieval uses the configured OANDA/Alpaca **read-only** connections and instrument registry. It saves at most 5,000 closed bars, preserves receipt time, calendar, price sides, adjustment and feed semantics, and rejects internal gaps. OANDA requests are limited to 5,000 calendar intervals. Alpaca requests stop at 150,000 constituent observations / 16 pages. Choose a shorter range when a provider limit is reached. This is retrospective data, not a point-in-time archive. Holidays and maintenance periods come from the saved calendar.

Forex experiments require actual historical bid/ask candles; midpoint plus an invented spread is rejected. Historical spread is charged once. Stock data keeps its existing raw/split adjustment without applying another adjustment; dividends and survivorship bias are not modeled.

## Install the optional engines

The API has no Qlib, NumPy, LightGBM or LEAN dependency. Install a separate Python **3.12** environment and the digest-pinned LEAN Docker image:

```sh
.venv/bin/python scripts/setup_quant.py --python /path/to/python3.12
```

On this Mac the bundled Codex Python can be used as `/Users/aoodie/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`. `--engine qlib` and `--engine lean` install only the selected engine. The validated Qlib dependency set is pinned in `backend/requirements.qlib.lock`; this lock was tested on macOS arm64/Python 3.12. Linux installations require their own smoke validation before deployment.

Run each worker in a separate terminal from the project root:

```sh
.venv/bin/python scripts/local.py migrate
.venv/bin/python scripts/local.py quant-qlib
.venv/bin/python scripts/local.py quant-lean
```

Back up the application database before a persistent upgrade. Migration `0034_quant_lab` adds a separate Lab table after `0033_worker_progress`; original datasets and replay history stay append-only. The Lab database trigger freezes session reports and each job's identity and input evidence, and permits only forward job transitions. Apply the matching API/frontend source update as part of that upgrade. Do not run an older API against the newer schema. The migration has been tested on disposable databases; live deployment is a separate operation.

These commands use the existing private local database settings without printing credentials. Workers expose readiness through their database heartbeat. They claim jobs with database locks, renew a lease, respect cancellation, enforce a 15-minute timeout and fail lost leases visibly. Run one worker per engine. Each owner may have eight active jobs. Engine logs and model text files are kept in ignored `artifacts/quant/<job-id>/`; back up this directory separately if model files are needed. Durable results, input and output checksums and runner provenance remain in Lab records.

For a server use a dedicated, supervised host research worker and an application-role database connection. Set `TAURIC_DATABASE_URL` privately and run `python -m tauric.quant_worker --engine ... --artifacts /private/research-artifacts`; provide `--python /path/to/qlib/bin/python` for Qlib. LEAN's host worker requires Docker access; **do not mount the Docker socket into the Tauric API or trading workers**. Its child container sees one job directory, has no network, drops capabilities and has CPU/memory/process limits. No provider, model, broker or database credential is passed into either engine subprocess. Installation does not enable or deploy these workers on a live server.

## Experiment design

Use ordered training, validation and test cutoffs. Each partition needs at least 20 samples after lookback and target purging. Features use only closed-bar prefixes; targets crossing a partition cutoff are removed. No full-dataset normalization is fitted. Features are versioned returns, volatility, range, close position, relative activity, historical spread and UTC clock phase. Tick counts, feed share volume and other activity are interpreted according to each dataset's semantics.

Qlib receives a custom single-instrument `DataHandlerLP`/`DatasetH`, not a stock-specific default universe or Alpha158 label. LightGBM uses a fixed seed and bounded tree settings. Validation chooses the boosting iteration; the test period is used only for evaluation. MSE, direction accuracy and return correlation are reported against a training-mean baseline. A final closed-bar forecast is an estimated return over the requested horizon. Targets include historical FX spread, but commission, slippage and financing are excluded from prediction accuracy. Synthetic smoke accuracy is not evidence of a market edge. Repeatedly examining or selecting models on the test period invalidates its role as unseen evaluation; reserve a fresh holdout for final comparisons.

LEAN uses native Forex/equity orders with observed open/close tick snapshots, long/flat integer-unit sizing, quote-currency capital and no leverage. A causal moving-average baseline or a completed matching Qlib model supplies signals. Only test-period model predictions can supply orders; dataset checksum, session, dates, lookback and horizon must match. Orders fill at the next observed open. The final position is liquidated on the last close snapshot, represented one millisecond before the bar boundary to separate it from the next open. This is an explicit timestamp approximation; no intrabar path, stops, limit orders, depth or liquidity is reconstructed. Native LEAN fills apply bid/ask, slippage, per-unit/minimum commission, sell tax and explicit FX financing at 17:00 New York with configurable triple-rollover weekday. The configured slippage/financing are assumptions, not measured broker execution. LEAN exchange-hours filtering is disabled because the validated imported calendar is authoritative.

## OpenClaw and authority

OpenClaw can discover operation schemas and invoke `retrieve_history`, `session_research` and `quant_experiment` through the existing owner-bound, audited project tools. Read `research_lab` for engine status and study IDs, and `lab_record` for bounded results. Inputs must be concrete; unavailable credentials, missing data or offline workers are reported. The original native analyst report, approval and order gates remain authoritative. Lab reports, models and backtests cannot be substituted for native artifacts or used directly to authorize paper/demo/live trading.

## Verification

The implementation passed the full existing backend regression run (1,014 tests), 11 deployment tests, frontend build/format checks and OpenClaw SDK build/validation checks. Additional Lab tests cover DST, daily bars crossing midnight, purged labels, evidence cutoffs, ownership, idempotency, cancellation, quote requirements and database input/terminal-result guards. Real isolated Qlib training and a matching LEAN handoff completed using synthetic historical prices in a disposable database. Independent native LEAN Forex and stock smoke runs produced fills, with stock commission and sell costs applied. The built UI passed report creation/export and queue/cancellation checks at desktop and mobile sizes.

These tests validate integration and boundaries. They are not evidence of profitable trading, calibrated forecasts or a live server installation. To repeat engine and browser checks after installing runtimes and building the frontend:

```sh
TAURIC_TEST_QUANT_RUNTIMES=1 .venv/bin/python scripts/local.py test tests/test_quant_runtimes.py tests/test_quant_browser.py -q
```
