# Automatic multi-setup scanners

Scheduled discovery and automatic research are separate saved controls. Enable both on a profile to scan with the browser closed and send qualifying published candidates to Tauric without pressing a research button. Existing profiles retain their previous settings until revised.

Select multiple setup families in the same profile:

| Family | Deterministic closed-bar condition | Direction |
| --- | --- | --- |
| Long breakout | Close strictly exceeds every prior high; configured activity ratio passes | Long |
| Short breakdown | Close strictly falls below every prior low; configured activity ratio passes | Short |
| Trend pullback | Recent-half mean establishes the trend. The previous close is on the pullback side of the prior full-window mean; the new close crosses back through that mean. Activity ratio passes | Long or short |

Trend pullbacks require at least four prior bars. Forming candles never qualify. All selected families share one market-data fetch per instrument, then compete under the same profile ranking limits. The score uses the existing bounded activity term and positive percentage distance beyond the family reference: prior high for breakout, prior low for breakdown, prior mean for pullback. It measures detector evidence; calibration remains future work.

Crypto exchange volume, FX tick activity and selected stock-feed share volume retain their original meanings. New families retain the existing FX calendar verification and stock session/window checks. The original long stock detector and executable trading rules are unchanged. A short research candidate grants no permission to short a spot or long-only account.

`scanner_worker` remains deterministic and never loads model connections or starts a model. It publishes gate-approved ranked candidates to existing immutable scanner events. The separate `candidate_research` service runs `tauric.candidate_research_worker`, consumes publications only from current enabled profiles with `automatic_research_enabled`, and calls the existing native research admission service. The native worker performs the model work.

Admission and its completion receipt commit atomically. Deterministic request identifiers and a coordinator lock prevent duplicate admissions across restarts. Only published candidates are consumed, and admission rechecks eligibility, positions/orders, processing state and current ranking. Queued native jobs recheck the profile revision, automatic permission, candidate context and ranking before launching models.

Compatible daily research is reused, including failed/interrupted jobs; a failed job never becomes an automatic paid retry. Newly admitted jobs count against the existing stable-profile daily allowance and any configured shared allowance. Queue capacity and native-worker readiness still apply. Exhausted daily allowance waits until the next UTC day. Temporary queue/connection/worker failures wait 30 seconds. Expired setups are skipped. Compact reason codes and job links appear in candidate history.

The interface exposes setup-family selection, scheduled screening, automatic research, family/direction labels and coordinator readiness. `detector_ids=[]` preserves legacy single `detector_id` behavior. New `automatic_research_enabled` defaults to false, so upgrades do not activate research for other profiles. No schema migration is required.

This coordinator grants research authority only. Account policy, reviewed execution authority and existing position-management services govern trading separately.

Verification: 872 backend checks passed (18 optional browser cases excluded), and 122 affected checks passed including the built scanner browser workflow. Eighteen dedicated automatic-scanner cases cover multiple families, shared ranking, stock and FX evidence, opt-in admission, restart deduplication, allowances, disabling queued work, ignored candidates and failed-job reuse. The browser confirms saved controls survive reload and the form fits desktop and mobile. Frontend production build, formatting and changed-backend lint checks pass. A cache regression also verifies that future-dated receipts cannot hide current market snapshots; autonomy tests use a fixed simulated same-day clock.

The owner-authorized preview rollout is deployed on 5 October 2026. A private database backup was taken beforehand. All 113 backend modules match the workspace across ten backend services, and served frontend assets match the production build. The coordinated market-intelligence fix is included. Existing model/data connections, strategy versions, trading policies, broker routes and shared budgets retain their baseline checksums.

Only `BTC / ETH rollout` was revised: all three families, scheduled discovery and automatic research are enabled, preserving its one-new-job-per-UTC-day allowance, configured external local AI route and existing thresholds. At the 06:28 UTC checkpoint, six real Binance cycles had run; fresh candles were evaluated for all six instrument/family combinations when they changed. No setup passed yet, so automatic research had admitted no new live job and used zero allowance. Automatic dispatch and reuse are verified with disposable fixtures; the prior genuine BTC discovery-to-native-research run remains recorded in the architecture document. The coordinator is healthy and will admit the next qualifying publication without a browser action.

Evidence: `artifacts/automatic-scanner-regression.log`, `artifacts/automatic-scanner-affected.log`, `artifacts/independent-scanner-browser-results.json` and `artifacts/automatic-scanner-preview-verification.json`. Repeat the read-only deployed check with `python scripts/scanner_preview_check.py`. The rollout helper's `enable-automatic` action is idempotent and preserves other saved profile settings.
