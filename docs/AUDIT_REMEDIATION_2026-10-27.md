# Audit remediation — 27 October 2026

Status: **in progress**. Application changes below are implemented in the working branch and are not yet a verified production release. The original audit remains a point-in-time record at `63c7e1984dc457dde60ae526037f56643be58126`.

The owner requested fixes and an independent backup folder. Routine tested release/recovery remains authorized. Financial workers, risk settings, failed research, historical evidence and explicit pauses retain their existing controls.

## Implemented changes awaiting release verification

- S01: workspace JUnit/TAP counts are advisory and cannot establish `tests_passed`. A fixed API smoke verifier executes as container UID 65534, separately from submitted code at UID 10001, with isolated Python imports and read-only workspace source. Its four named cases, inventory hash and source checksum are signed by the host. It certifies only that explicit profile, not the entire unit suite. Hosted engineering acceptance also exercises fabricated reports against a deliberately broken API.
- S02: completion criteria require exact evidence bindings and a freshness window. Wrong subjects, stale/future records and unbound legacy descriptions fail completion. Explicitly selected existing evidence remains age-bounded. Source, command, instrument, child-objective and limited-verifier identities have distinct bindings.
- E01: owner-authenticated scope removal can waive a cancelled child; cancellation alone never satisfies a dependency. The reason and owner remain recorded, and waits preserve the disposition.
- E02: host output carries cumulative truncation and observed byte counts. Unknown legacy completeness stays unknown rather than falsely complete.
- E03/E04: provider requests execute through a process with a total deadline, bounded output and no automatic retry. Sanitized transport/status/schema diagnostics retain the original research reservation. Keys and raw provider error bodies are excluded.
- E05: required browser coverage expands from five to nine existing workflows. The assistant/scanner fixture now explicitly selects its legacy scoring contract and waits for the real scan response; an async browser predicate did not actually wait for completion. Production scoring rules were not relaxed.
- E06: CI regenerates and compares runtime OpenAPI and formatted TypeScript in temporary storage. An explicit write mode updates both contracts together.
- C05: capability labels distinguish implementation, configuration and per-request verification. Execution authorization remains per account in Trading. Stale ownership implementation wording is corrected.
- O01/O03: local monitor failure records, additional worker/goal signals and backup-age checks are implemented. Fixed unit definitions add bounded same-day backup retries; uncertain backup phases still require reconciliation.

## Independent backup and observer installed on the owner's Mac

The private `Tauric Backups` directory in Documents contains the encrypted October 26 and 27 checkpoint pairs and authenticated copy receipts. Each ciphertext matched its Oracle hash and decrypted in a streaming authentication check without writing plaintext. Recovery key material is retained separately in a private directory. Oracle's checkpoint reports now reference verified off-host receipts.

A user LaunchAgent runs a pinned copy of the observer/pull tools every fifteen minutes and at load. Its Python environment is now private and outside cloud-offloaded Documents storage. Its first independent run succeeded and submitted a macOS notification for the root project goal requiring owner attention. Unchanged status is quiet; failed delivery is retried. The observer checks Oracle monitor freshness, task availability and independent backup recovery.

This is conditional on the Mac being online. It is not an always-on third-party monitor or an independently acknowledged paging service. Local installed tool hashes and runtime receipts are retained outside the repository; no SSH key or recovery key is committed.

## Security scan and dependency remediation

A pinned Trivy 0.76.1 scan of the Python and Node locks found a critical certificate-store advisory and eleven high-severity records. Frontend production dependency audit reported zero findings. The Trivy database initially exceeded a temporary filesystem; the successful scan used an independent cache and retained its complete report. No failed scan is represented as a pass.

The certifi advisory is confirmed by the [upstream security advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2). All three Python locks now target 2026.10.18. The images update the Node security base to 24.22.1, Python and Caddy base digests, available Debian fixes, Vite 8.3.3 and targeted OpenClaw libraries while its core pin remains 2026.9.7. Hash-pinned pip backports replace private msgpack/urllib3, remove the deprecated metadata backend, update the component inventory and rebuild its venv bootstrap wheel. Eight real container tooling checks pass, including offline installation and import. Source changes do not establish that Oracle's images or separately installed research environments have been updated.

Research runtime dependency changes require their own tested, recorded installation. The unchanged-dependency source-binding guard must remain enforced; an actual dependency transition and independently measured installed inventories are required before resuming those workers. Do not edit historical source manifests or pretend changed dependencies are unchanged.

Final image scanning is now a required release job on both amd64 and arm64, before bundling. Unfixed residual OS/tooling findings have exact, expiring technical exceptions documented in [the reachability review](SECURITY_EXCEPTION_REVIEW_2026-10-27.md); raw findings remain visible. Available fixes, changed scope, scanner errors or expiry block release. These are Codex technical assessments, not fabricated owner approvals. The final hosted images and production runtimes still require verification.

## Remaining work against the full audit

| Finding | Current disposition |
| --- | --- |
| S01/S02 | Implemented; local adversarial contract checks and real isolated API smoke pass; full hosted release and production proof pending |
| S03 | Financial database/key separation is not implemented; requires a data-only gateway and service-specific authority, not just renaming a role |
| S04 | Unified owner-wide monetary reservation is not implemented; existing allowances stay unchanged |
| S05 | Real dependency findings are being patched; release image scan policy and exact runtime installation pending |
| O01 | Independent Mac observer installed; host changes pending release; always-on external coverage remains separate |
| O02 | Verified independent copies and recurring pull installed; subsequent unattended checkpoint still needs observation |
| O03 | Safe retry/freshness implementation awaits installation and failure drill |
| O04 | Representative 24-hour operating acceptance remains incomplete |
| O05 | Terminal engineering workspace archival is not implemented |
| C01 | Existing goal still requires owner after budget exhaustion; do not reset it or claim ownership acceptance from operator work |
| C02 | No qualifying prospective strategy or complete governed connected cycle; financial workers remain disabled |
| C03 | IBKR/Binance execution and futures still require implementation and actual test-account acceptance |
| C04 | Fixed existing-service recovery bridge is not implemented |
| C05 | Truthful capability presentation implemented; release pending |
| C06 | Matched model/rules shadow contribution study remains incomplete |
| C07 | Guided interface remains an explicitly optional post-launch enhancement |
| A01 | Independent archives exist; replacement-host recovery objectives and drill remain unproved |
| A02 | Ledger replay/lock scaling improvement remains pending measurement and implementation |
| A03 | Fair scheduling and workload guarantees remain pending |
| E01–E06 | Implemented as described above; final exact-revision checks and deployment still pending |

## Verification record so far

The initial comprehensive backend run had 1,298 passes and one obsolete capability assertion; the corrected assertion passes. Subsequent focused suites cover the changed task, receipt, release, provider and deadline paths. Host tooling (111 checks), strict TypeScript/build, formatted contract generation and OpenClaw schema/plugin checks have been exercised. All nine required browser workflows passed before the final Vite patch. Later full Mac runs were interrupted after cloud-offloaded dependency/cache/output files stalled reads; they are not passes. Complete exact-source verification is assigned to the clean Linux release workflow. The immutable API smoke profile passed four actual HTTP checks in a network-isolated read-only container under its separate identity. Final counts and the exact deployed revision must be recorded after the complete checked release.

Evidence is retained in the ignored `artifacts/audit-remediation-20261027/` directory. Passing fixtures, this document and a submitted workflow do not establish deployed completion.
