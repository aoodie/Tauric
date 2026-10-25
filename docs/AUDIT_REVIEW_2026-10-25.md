# Tauric — corrected audit and remediation

Reviewed 25 October 2026. Source baseline: `4e0e956a3bef364a6edf11f65b5cd4202adf078d` in `/Users/aoodie/Documents/Codex/Tauric`. Remediation work follows that baseline on `codex/audit-reliability-20261025`.

This corrects the supplied deep read-only report. Its architecture and acceptance conclusions are broadly supported, but several findings confused historical records with current state or understated existing capabilities. The original report was not edited. Local implementation and deployment status are distinguished below.

## Independently reproduced baseline

- Clean checkout; baseline HEAD and the locally recorded `origin/main` both equal `4e0e956`. Relative to recorded production `1b45227`: four commits, 46 changed files, 1,733 insertions and 107 deletions.
- 644 tracked files; 171 backend modules containing 40,980 lines; 26,064 lines of backend tests; 45 TSX files and 37,705 frontend TS/TSX lines; 5,766 frontend CSS lines; 19,360 host-script lines excluding tests; 69 Markdown documents; 37 migrations.
- 1,251 non-browser tests collected with the external quant-runtime tests excluded; 26 browser tests deselected. Collection is not execution.
- Generated application OpenAPI exactly matches the saved contract: 160 paths and 238 schemas. Backend/script lint and frontend formatting passed.
- The reviewed source implements Decimal-based authoritative ledger/sizing paths, database-enforced immutable history, request identities, locks, leases and unknown-outcome reconciliation. These mechanisms and passing tests are evidence of controls, not a proof that every financial or security path is defect-free.

## Corrections to the original findings

| Claim | Corrected finding |
| --- | --- |
| Source manifest drift is a maintenance defect | The 391/504 matching hashes and 113 differences reproduce, but `github-source-manifest.json` explicitly identifies itself as a historical source-export record. Preserve it. Current identity comes from Git, the release manifest and source-bound images. Some status documents were stale and are updated separately. |
| No metrics or structured logging | `operations.py` already persists attempts, successes, failures, durations, invocation IDs and failure categories; it exposes queue and unresolved-order state and emits JSON failure events. Consistent logging/export and actionable alerting remain incomplete. |
| Oracle is overcommitted on a 4 GB host with about 2 GiB swap | Fresh Oracle inspection found 16,765,104,128 physical bytes, 6,532,345,856 available bytes at the sample and zero configured swap. The legacy 4 GB recovery record is historical and does not describe current Oracle. A snapshot does not establish peak-workload headroom. |
| Active source limits total 5,020 MiB, PostgreSQL 732 MiB | Normalized source Compose limits total 5,056 MiB for the twelve intended long-running services; PostgreSQL is 768 MiB. Actual deployed limits total 5,568 MiB because the deployed recovery override allows OpenClaw 1,536 MiB. Limits are ceilings, not measured consumption or reservations. |
| Paper stops are not monitored | Basic paper stop references size risk and are not broker-held protective orders. Opt-in executable strategies monitor closed-candle exit rules, and opt-in autonomous paper execution can close attributed positions. Neither guarantees the stop-price fill. |
| Local read-only review proves current production state | The original report relied on dated status records and explicitly did not SSH. The direct inspection below supplies a new, timestamped observation; it does not retroactively broaden the original verification. |

## Fresh Oracle observation

Pinned-host SSH inspection completed between **20:26 and 20:33 UTC on 25 October 2026**. Only host/container reads, file hashes and read-only SQL were performed. No broker API, model request, deployment, restart or settings change was made.

- Active release and API image revision: `1b4522732ec3a1bed7a28d6f851191e73554c455`. Source corrections on main are still not deployed.
- Twelve intended Tauric services were running and healthy, with zero restart counts and no OOM flag on those current container instances. No financial execution workers were running. Additional historical restore databases were observed running under separate projects; they were not stopped by this review.
- The active release manifest has 45 files. Two differ: `deploy/compose.https.yml` and `deploy/Caddyfile.https`. The mounted Caddy configuration contains QuantDinger routing. Its SHA-256 is `4fcf72894d16bb126427635d0410671c34be161632be4109ca767153b50b1662`. Shared ingress preservation remains a real release constraint.
- Read-only SQL found zero rows in `broker_policy_versions`, `broker_routes`, `broker_order_intents`, `qualification_reports` and `trading_readiness_records`. This does not assert that external broker accounts have no holdings.
- Sanitized local observations are retained under `artifacts/audit-reliability-20261025/` as `oracle-preflight.json`, `oracle-identity.json` and `oracle-release.json`. These are observations of the running host, not a representative load test or autonomous ownership acceptance.

## Implemented locally

1. **Database-test isolation.** Before test database creation or deletion, verify the generated disposable name, loopback URL, local Unix-socket Docker endpoint, exact checkout Compose configuration, published database port and PostgreSQL cluster system identifier. A tunnel to another cluster is refused before DDL. Cleanup also runs after fixture setup/migration failures.
2. **Assessment failure identity.** Provider failures, oversized/invalid input and malformed/mismatched responses raise fixed sanitized failure categories. The native process exits unsuccessfully and the worker records a failed job rather than fabricating an `incomplete` strategy verdict. Valid model decisions of `incomplete` remain verdicts. Failed/billed work is not automatically retried and existing failed admission identities remain retained.
3. **Retained credential keys.** Support legacy single-key files and staged MultiFernet rotation with retained historical keys. Staging is private and cannot overwrite the active file. Source-export scanning checks every retained key. Saved broker credential revisions remain immutable. See [activation and recovery requirements](CREDENTIAL_KEY_ROTATION.md).
4. **Shared ingress guard.** Before a new release's first installation effects, compare both Caddy files and both ingress Compose files with the active release manifest. Reject missing, symlinked or modified ingress rather than overwriting unrecorded shared routing. Existing recovery reentry continues to preserve its recorded service set. This prevents silent replacement; it does not itself move QuantDinger into a separate ownership layer.
5. **Measured host telemetry.** Soak observations now include host physical/available RAM, swap, active container memory ceilings and unbounded services. Unsupported/malformed host memory data is unavailable rather than guessed. Native-worker startup/storage events use JSON. The existing per-lane metrics and status remain in place.
6. **Source and status clarity.** Bind the assessment worker and credential-loading implementation into the execution fingerprint; clarify paper exit monitoring; extend the PR index through #20; separate current requirements coverage from the historical foundation table; label the legacy memory record correctly. The historical source-export manifest remains unchanged.

## Remediation verification

| Check | Result |
| --- | --- |
| Complete non-browser backend suite, external quant-runtime tests excluded | 1,279 passed; 26 browser cases deselected |
| Mandatory built-frontend browser workflows | All five executed and passed; skipped cases are rejected by the runner |
| Host deployment/tooling suite | 72 passed on the configured Python 3.13 environment |
| Backend/script lint and patch whitespace | Passed |
| Frontend type checking, production build and formatting | Passed; the existing bundle-size advisory remains |
| Generated OpenAPI versus saved contract | Exact match: 160 paths, 238 schemas |
| Historical source-export manifest | Preserved unchanged |

The focused assessment, database-guard, credential-key and broker-connection suite also passed all 82 cases. These checks use disposable local databases and synthetic provider evidence. The separate real Qlib/LEAN runtime lane and native Python 3.10 host lane were not rerun locally. No deployment, representative 24-hour workload or connected trading qualification is claimed.

## Release and remaining work

These changes are local until a tested, source-bound release is installed and independently verified. Neither tests nor this document qualify a strategy or activate an account.

1. Reconcile shared ingress ownership and preserve QuantDinger's exact routing through the controlled release process. The new source guard intentionally rejects the currently modified release files. Do not bless a modified manifest or bypass the guard to force an installation.
2. Satisfy the existing separate trusted qualification requirement for a changed execution fingerprint, preserve a verified recovery backup and the exact running service set, then verify source/image identity, settings/history and readiness after installation. The existing signature rejection remains intact.
3. Measure a representative workload on the actual 16 GB Oracle host before changing RAM or service limits. Extend existing telemetry with consistent export and actionable notifications; do not infer an OOM incident from aggregate caps.
4. Complete the standing OpenClaw ownership acceptance with real executed engineering, governed release, restart/resume and bounded recovery evidence. Preserve the owner's paused legacy goal and financial boundaries.
5. Continue bounded preregistered research under existing allowances. Only a genuinely passing current research result can progress to the existing prospective demo protocol on a suitable dedicated account. Failed trials remain failed; fixtures and retrospective bars do not replace elapsed observations.
6. Prioritize ledger checkpoints/retention from measured growth, incremental typing/component tests and dependency/image/provenance verification. Plaintext local dump storage still needs a separately verified encryption/recovery cleanup; supporting key rotation alone does not resolve that exposure. IBKR/Binance execution and the remaining platform scope remain tracked, not silently declared complete.
