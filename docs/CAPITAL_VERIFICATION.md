# Shared capital verification — development 0.5.0

Fixed USDT paper portfolios and nested capital pots are implemented locally. This stage does not certify live trading, multi-broker routing, real model research or server installation. AI connections remain available for configuration later. No real owner balance or allocation is seeded.

## Owner workflow

In **Settings → Trading**, create a paper account with your explicit starting balance and account policy. Add a portfolio with a name, fixed USDT budget, aggregate risk percentage, position limit and reason. Add optional capital pots under that portfolio or an existing pot. Names are generic data; no Forex/Crypto/Intraday category is hardcoded. Select a portfolio or pot when creating a paper strategy. Strategy capital cannot initially exceed the selected allocation budget.

Allocation cards show account, ancestry, current budget, capital reserved for children, descendant acquisition costs including entry fees, position count and revision. **Edit allocation** saves limits with a reason and expected revision. **Allocation history** preserves prior budgets/reasons. The capital resource includes current state and complete version history. Parents, names, kind and account are fixed identities in this stage; reparenting, renaming and deletion are not implemented.

## Enforcement

- Portfolio budgets in an account cannot sum above its fixed starting capital. Realized gains do not automatically enlarge allocations; losses and marked exposure still constrain entries through account cash/equity rules. There are no transfers or independent pot cash accounts.
- Child budgets reserve capital from their parent. Direct parent strategies use only the budget remaining after child allocations. Every descendant position also consumes the shared capital, aggregate open-stop risk and position limit of each ancestor. Overlapping strategy capital limits are allowed because the shared planner prevents simultaneous overspending.
- New-entry capital reserves existing quantity times the greater of entry/current price, plus entry fees. A falling mark cannot free an open position's acquisition commitment. New entry fees count against available allocation capital. Aggregate risk uses current marked stop-reference exposure and modeled exit fee/slippage, together with existing account/strategy risk constraints.
- All sizing uses Decimal precision 120 and owner-specified quantity steps. A remainder below the next strategy's step cannot fund an entry. Fresh observations are required for all held instruments at planning and immediately before a fill. Changed marks that invalidate capital/risk capacity block the old plan.
- Budget edits and child creation cannot reserve more than a parent's budget after direct open-position acquisition costs. Editing checks immutable acquisition costs without requesting market data. Later price moves can exceed a fixed budget; new entries then block, while owner closes remain available. Lowering risk/position limits can leave existing positions over the new threshold; it never creates an automatic exit or reassigns their history.
- Account row locks serialize allocation writes, planning and fills. Creates are idempotent by owner/request identity. Edits require the latest allocation revision. Proposals capture the account's allocation digest; governance captures the allocation state. Allocation changes advance account revision and invalidate earlier proposals, decisions, plans and pending model permissions.
- Plans and fills retain selected scope ID, complete ancestor IDs/version IDs/limits/calculations, strategy/version/deployment/account/mode, decision and native evidence links. Exits retain the exact opening lineage even after allocation edits. Original native reports remain unchanged.
- Restricted application SQL cannot update, delete or truncate allocation history. Insert guards enforce contiguous revisions, immutable identities, existing initial parent identities and owner/account consistency. Parent identities cannot be changed, so cycles cannot be introduced through the API. The operational nesting bound is 16 levels.

## Existing data

Migration `0007_capital_scopes` adds history without rewriting deployments or trading records. Existing labels remain readable. Accounts without shared allocations retain the earlier owner-authored paper behavior. To allocate an existing account, close its unassigned legacy positions first. Once the account has a portfolio, unassigned strategies cannot open new entries; create a new deployment with a selected shared allocation. This avoids fabricating budgets or silently changing historical attribution.

## Evidence

- **111 backend checks passed**, including 15 new shared-capital cases and one additional governance case. Coverage includes typed decimal/parent inputs, root/child over-allocation, direct parent reservations, ancestor risk/position limits across siblings, fees, close/release/lineage, revision conflicts, idempotency, immutable SQL/insert guards, cross-account rejection, legacy compatibility, stale proposals/plans/model decisions, concurrent child creation/fills and changed held-instrument marks immediately before fill, and rejection of replaying an exit plan as an entry.
- **5 paper browser workflows passed** with actual public Binance data and disposable balances, now including portfolio/pot creation and assigned strategy setup before owner review → plan → simulated fill → explicit close.
- **5 capital browser checks passed** for nested pot creation, edited budget history, stale revisions, visible over-allocation rejection and mobile controls/history without horizontal overflow or page errors.
- **3 paper availability checks passed**: storage failure remains visible, recovery clears the load error, and successful polling preserves action validation errors.
- **5 built Linux-container checks passed** for frontend/API readiness, persisted nested revisions/history/resources, authenticated reads/CSRF, Caddy private-route rejection and desktop/mobile rendering. They make zero AI model or broker order requests.
- Backend lint/format checks, generated public OpenAPI/client types and production frontend builds passed. The local development stack migrated to the new head and reached readiness with native/OpenClaw/governance services still separate.

Ignored screenshots/results are in `artifacts/`; fixture databases/keys/owners are disposable. Account policy inheritance through the generic settings service, strategy version registries, allocation methods beyond fixed amounts, moving/renaming scopes, full autonomous native requests and broker routing remain pending. Existing stops are sizing references, not monitored protective orders. Linux amd64/IONOS measurements, backup/restore, rollback and the full release gate remain pending.
