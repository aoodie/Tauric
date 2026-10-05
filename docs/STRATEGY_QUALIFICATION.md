# Strategy qualification

The workstation now registers and evaluates an exact crypto breakout strategy version, saves separate training/holdout/cost-stress evidence, and requires a distinct exact-evidence Tauric strategy verdict. This is research-comparison qualification. It grants no deployment, promotion, capital-transfer or order authority.

## Owner workflow

Open **Research & analytics → Strategy qualification**, choose a portfolio with a saved mandate, and register a protocol against a saved executable breakout version and crypto dataset. Enter the hypothesis, training/embargo split, explicit experiment costs and risk, stressed costs, sample/return/drawdown criteria and assessment lifetime. Numerical qualification thresholds start blank in the form. Nothing is enabled or configured on the owner's behalf.

Freeze first, then evaluate. The protocol pins the strategy and mandate revisions, actual mandate/objective, linked account policy records, dataset checksum, split, assumptions, replay/assessment source snapshots, thresholds and stressed costs before results exist. Each protocol can be evaluated once. Repeating the same request returns its saved evidence; changing its inputs or trying a new evaluation request for the evaluated protocol is rejected. New hypotheses require new protocols, preserving rejected trials.

Training and holdout windows start flat, warm up independently and liquidate at their own final closes. Embargo candles are not traded. Each stress scenario separately replays the identical holdout under frozen higher fees/slippage. There is no holdout-driven parameter selection. The report evaluates coverage, closed-trade sample, after-cost return, close-sampled drawdown, registered data reuse, current mandate/risk/source validity, intended mode and exact-version native assessment.

Registered ordinary replays and earlier qualification windows are checked for overlap by instrument, interval and time, even when data was imported again with a new identifier. Earlier training windows also count as exposure. Reused holdouts fail the registered unseen-data criterion. Raw data can already be known to the owner; prior owner knowledge and optimisation outside this platform cannot be verified. This limitation is shown in the form, results, model input and reports.

## Exact Tauric assessment

The owner explicitly selects a configured cloud or external-local connection and requests the assessment. It goes through the existing native queue, worker lease, shared admission budget and pinned TradingAgents 0.5.2 runtime at revision `8b22d43d01d9ddda5d686d093d5385884622f3de`.

The original native graph still produces its current daily background reports and instrument BUY/HOLD/SELL rating unchanged. The explicit `tauric_exact_strategy_assessment_v1` extension then uses that graph's deep model through its structured-output interface to review the frozen strategy, hypothesis, split, metrics, stressed scenarios, exposure and limitations alongside those original reports. It produces a separate suitable/unsuitable/incomplete verdict and must echo the strategy version, evidence checksum and intended mode exactly. This is an explicit platform extension to Tauric; upstream daily instrument output does not itself assess a strategy.

Malformed, unsupported or mismatched output produces an incomplete verdict, with no fallback to the daily rating or automatic rerun of the assessment job. The extension makes one structured review invocation; underlying provider transport behavior remains governed by the native client. Matching same-day requests reuse the immutable admission, including a failed job. New admission still uses the shared AI allowance. Queued assessments validate current source, mandate, risk, model connection revision/fingerprint and native settings before model execution. Report snapshots repeat those validity checks and apply the owner-selected assessment lifetime.

A normal daily BUY, missing verdict, different version/evidence/mode, wrong runtime/source, expired assessment, changed model/settings, or an unsuitable verdict cannot create a passing exact-assessment criterion. Strategy qualification artifacts are also rejected by paper-entry and governance entry-context paths. Automatic paper workflows continue to require their separate applicable daily research. Protective exits retain their deterministic safeguards.

## Records and access

Migration `0018_strategy_qualification` follows `0017_portfolio_research`; the parallel read-only market adapter migration is `0019_market_adapters`. Protocols, evaluations, admissions and sequential report revisions are owner-scoped and append-only, including database mutation/truncate guards and owned lineage checks. Prior incomplete/failed/passed reports remain readable and are labelled with their recorded time. A fresh report appends a new snapshot rather than rewriting an old result.

Portfolio chat receives a compact read-only qualification summary and can read the original native artifact only in its bound portfolio. The UI shows separate window metrics, every criterion, job status, report history and the full frozen evidence. Owner login and the existing CSRF protections apply to the new API routes under `/api/v1/qualification`.

## Verification and limits

The final combined backend regression passed **397 tests** across qualification, autonomous paper, market adapters and encrypted market connections. The final credential/detail-route checks also passed (16 cases), as did the qualification/portfolio checks, production frontend build and formatting. Evidence is in `artifacts/qualification-combined-final-regression.log`, `qualification-final-scoped.log`, `qualification-market-credentials-final.log` and the browser/native compatibility records.

The local preview at `http://127.0.0.1:8080` is upgraded through `0020_market_connections`, following qualification and the market adapters. The private pre-upgrade backup was restored and migrated in a disposable database; application-role reads and preserved history counts passed. Seven running backend services match the tested source, and the served frontend matches the built assets. Owner history/model connections remain intact, and qualification records, market credentials, daily research policies and autonomy policies remain empty. No model jobs were started by verification. See `artifacts/qualification-preview-verification.json`, `qualification-migration-verification.json` and `qualification-backup-verification.json`.

Disposable PostgreSQL checks cover frozen splits, future-bar leakage, deterministic costs, repeated/re-imported data, immutable history, exact native bindings, adverse/missing verdicts, expiry, portfolio isolation, budgets and rejection of qualification as entry approval. A built-browser fixture exercises registration, evaluation, missing-assessment incompleteness, exact-version assessment, retained report history, original-report inspection and desktop/mobile layout. Browser/native outputs are explicitly synthetic.

`artifacts/qualification-native-compatibility.json` records construction of the separate structured schema on both actual pinned native model routes and invocation of the extension with an offline fixture. It made zero provider model calls. A connected real strategy-assessment run has not been certified; earlier real daily research does not count as that evidence.

OHLC next-open fills assume liquidity and do not establish latency, order-book spread, partial fills, broker execution or live scalping performance. Paper execution and live-scalping intended modes therefore remain incomplete even with passing numerical results and a suitable model verdict. Forward/shadow observation, additional executable families, point-in-time news/AI replay, daily automatic qualification, strategy promotion and lifecycle recovery based on this qualification remain further work. FX/stock data adapters do not make those assets eligible for this native qualification path.
