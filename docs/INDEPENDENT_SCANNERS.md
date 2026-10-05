# Independent scanner profiles

Scanner profiles now work without a strategy, paper account, allocation or deployment. In **Settings → Scanners**, choose **Add scanner profile**, give it a name, select instruments and closed-candle screening parameters, choose a research connection and allowance, and save. **Scan now** runs manual deterministic screening. Profiles remain manual by default. Opt-in scheduled screening requires the scanner worker; automatic research is a separate opt-in control handled by the downstream coordinator.

Each profile has its own stable identity, version history, name and per-UTC-day research allowance. Multiple profiles can screen the same evidence; candidates deduplicate while keeping each matching profile and expiry. The candidate research selector uses scanner names. Watch/ignore, expiry, evidence inspection and chart linking also work without strategies.

The strategy link is optional and can be added, changed or removed by saving a new profile revision. Each saved revision retains its exact link. Naming, filter edits and link changes preserve the profile identity and charged research count. Multiple named profiles can link to the same saved strategy version. Research still requires a configured connection and an available native worker; candidates grant no execution permission.

## Compatibility and upgrade

Migration `0023_independent_scanners` follows `0022_tradingview_mcp`. It backfills the independent identity of existing strategy-bound profiles from their strategy-version ID, preserving requests, checksums, matches, history and analysis records. Original requests can be replayed idempotently. Legacy unnamed strategy-bound clients retain their one-profile-per-strategy identity. The existing profile-history URL remains usable for those profiles because their identity is the old strategy-version ID. New clients use the returned `profile_id` for history and revisions; `id` continues to identify an exact saved revision.

Research allowance accounting now follows profile identity across revisions. Existing charges are counted through their original profile-version references. Optional strategy references remain subject to owner and exact-version checks, and database triggers still enforce ownership, sequential revisions, immutable evidence and matching analysis references, including unlinked profiles.

Back up the database and preserve the model encryption key before upgrading. Rebuild matching API/worker/frontend services and migrate before starting the new API. No scanner, account, strategy or automatic policy is created or enabled by the upgrade. Restore a verified backup for rollback; history migrations do not delete immutable records.

## Verification

- Backend checks cover creation without strategies, idempotency, names and reference validation, independent histories, shared candidates, owner boundaries, research admission/reuse, and allowances surviving edits and link/unlink changes. Final full backend regression: 554 passed; 12 optional browser cases excluded. The focused scanner/stock/migration/browser run passed 38 cases.
- A populated previous-schema database upgrades without changing its original profile/candidate payloads or checksums. Original request reuse, queued admission, existing research charges and restored immutable-history protection pass.
- Six production-asset browser checks cover creating profiles with no strategy/account, multiple profiles and shared candidates, fixture-only queued research, independent rename/disable/history, reload persistence and desktop/mobile rendering. Evidence: `artifacts/independent-scanner-browser-results.json` and the desktop/mobile screenshots.
- The shared preview is updated and verified across seven API/worker services, database schema, private API access and built interface. No scanner, strategy, paper account or deployment was seeded. Read-only evidence: `artifacts/independent-scanner-preview-verification.json`.
- Browser and backend market/model checks use explicit fixtures; no paid model or broker calls are made.

Deterministic setup scoring, downstream Candidate Gate/ranking, and opt-in scanner cadence are now implemented. See [candidate discovery architecture](SCANNER_ARCHITECTURE.md) for the current contract, controls and verification. Long breakout, short breakdown and trend pullback can now be combined in one profile. Separate automatic research admission is documented in [automatic scanners](AUTOMATIC_SCANNERS.md); supported crypto, Forex and stock/ETF data adapters are reused.
