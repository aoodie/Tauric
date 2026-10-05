# Strategy versions and paper routing verification

Executable candle rules were added after this historical versioning slice. See [executable strategies](EXECUTABLE_STRATEGIES.md) for the current on-demand paper entry/exit behavior, verification and remaining automation work.

Recorded 3 October 2026 for development 0.6.0. Immutable strategy versions and explicit paper deployment controls now work in Settings → Trading. The full platform is not ready for server installation.

## Implemented behavior

A strategy has a fixed identity/name and append-only parameter versions. Each version records its label, notes, quantity step, capital limit, risk percent, reason, request identifier, revision and checksum. Creation/revision requests are idempotent; revision conflicts fail rather than replacing another window's version. Owner isolation and database history guards protect the records.

A registered paper deployment selects one exact saved version and an explicit paper account/shared allocation. Parameters must match the saved version. Multiple accounts can deploy the same version while maintaining separate balances, positions and fill attribution. Creating a newer version leaves existing routes and positions unchanged. Deploying it creates an additional route; it does not automatically retire or transfer an older route. Pause an older route explicitly when appropriate. Historical label-based deployments remain readable and keep their lineage.

Pause/resume records immutable control history and a reason, checks the expected revision and advances the account revision. Pausing blocks new proposals, permission use and plan execution. Old proposals remain stale after resume and must be replaced. Existing attributed positions can still be explicitly closed; their original deployment and strategy-version references are preserved. Resource details expose control history and saved strategy versions. Shared allocation and account limits remain independently enforced.

## Verification

- All 120 backend checks pass, including eight strategy cases and a governance permission test for pausing during an active review. They cover owner privacy, request identity, revision conflicts, concurrent revisions, database immutability, parameter matching, unchanged old versions, multiple account routes, fill lineage, pause/stale-plan behavior, resume and explicit closes.
- Five strategy browser flows pass using a disposable owner and actual public Binance quotes: create/deploy v1, save v2 without changing v1, fill from the pinned v1, pause while a position is open and explicitly close it, then reload/mobile rendering. Five paper browser flows and three paper availability/error checks also pass.
- Five built Linux-container checks pass: migrated readiness, version/pause persistence across containers, owner/CSRF protection, private Caddy ingress and desktop/mobile rendering.
- Production TypeScript build and generated API types pass. Backend lint and formatting pass.

Browser fixture credentials are private ignored files. Test accounts/policies are labelled disposable fixtures, never seeded into the real owner's account. No model or broker order calls are made by these strategy checks.

## Limits

Saved notes/parameters configure paper deployment limits; they do not generate trading signals, implement a backtest or alter the native Tauric graph. Current execution is owner-authored long-spot paper proposals with explicit closes, or validated queued paper governance. Stops remain sizing references without automatic monitoring. No shorts, futures, partial closes, transfers, atomic route replacement, live accounts, multi-broker execution or autonomous scheduling are added. Full policy inheritance, capability registries and the complete connected OpenClaw workflow remain future stages.
