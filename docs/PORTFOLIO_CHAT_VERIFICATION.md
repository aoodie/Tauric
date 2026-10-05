# Portfolio workspace and scoped OpenClaw chat

Verified 3 October 2026. This implements the first bounded portfolio/chat slice from the [implementation plan](PORTFOLIO_IMPLEMENTATION_PLAN.md). It does not complete the [full portfolio/research design](PORTFOLIO_RESEARCH_DESIGN.md).

## Available behavior

Open **OpenClaw chat**, then choose a portfolio under **Conversation context**. Existing root paper allocations become selectable without rewriting their financial history. General conversations retain their original context. Each saved conversation has a permanent owner and portfolio binding; selecting another portfolio starts a separate context and lists only its history.

Save a portfolio mandate, research ranking objective, fixed/managed capital preference, linked account allocations and chat permissions in the expandable portfolio settings form. **Settings → Trading** uses the same form and versioned service. Managed capital is a recorded preference only; it does not transfer funds. Linked roots must be owned, cannot be shared across mandates, and cannot already have their own mandate or conversation. Multiple links preserve each account's existing cash, policy and allocation limits. Financial allocation revisions, strategy deployments and fills are preserved.

The portfolio workspace shows attributed deployments, open positions and allocation budgets/open cost. It does not present account cash or realized P&L as portfolio equity. Unallocated legacy deployments are excluded. Original reports remain separate and unmodified.

The daily research extension is documented separately in [daily research verification](PORTFOLIO_DAILY_RESEARCH_VERIFICATION.md).

Chat permissions default to disabled until saved. When enabled, an explicit owner request can change the bound mandate or research objective, or pause/resume an existing paper deployment. The assistant reads fresh server context and supplies the current settings/control revision. It cannot grant itself permissions, edit account links or financial risk limits, submit orders, approve governance, promote strategies or rewrite native reports. Pause preserves positions; resume enables existing paper rules and does not submit a trade. Each successful action atomically appends its normal configuration/control history, audit event and immutable before/after receipt, shown in the conversation. A duplicate tool call returns the original receipt; a different retry, stale revision or rejected request does not create a success receipt.

The plugin derives identity from the pinned Gateway tool factory, checks the invocation at the final network effect, and does not accept model-supplied portfolio/owner/session targets. The server resolves the active leased turn's portfolio. Scoped research reads require a research admission linked to that portfolio; a report/job from another portfolio is unavailable. Explicit daily BTC/ETH/SOL research requests are restricted to instruments already deployed in the selected portfolio for this slice. General owner web chat and configured owner DMs retain their existing bounded daily-research route and shared allowance. Portfolio routing for Telegram/Discord remains pending.

## Tauric dependency

The portfolio tools have no entry or promotion authority. Existing automatic paper entry validation now additionally requires a verified native `BUY` result. Native `HOLD`, `SELL`, missing or unsupported ratings block before OpenClaw review; the same original-evidence function is used again before entry execution. The full automation suite verifies stale/missing/incomplete evidence and unchanged original artifacts. Native graph code and pinned upstream revision are unchanged. Protective exits retain their existing deterministic path.

This check does not certify every historical manual paper workflow as having a mandatory native gate. The complete profile-aware qualification and promotion contract remains later work; portfolio chat does not add an alternative order path.

## Verification evidence

- Full backend suite: **309 passed**, two optional browser workflows excluded; disposable PostgreSQL databases, no model calls.
- Portfolio backend cases include version conflicts, request retries, competing edits, multiple account links, permanent bindings, portfolio filtering, disabled permissions, expired/foreign invocations, action rollback, cross-portfolio deployment/report/job rejection, unchanged original reports, and pause/resume with an existing synthetic paper position and unchanged ledger.
- Built frontend portfolio browser workflow: **passed**, covering mandate save/history, stale conflict/reload, separate histories, durable pause receipt, reload persistence, and desktop/mobile layout. It exercises the real chat worker with an explicitly offline gateway fixture.
- Existing built executable-strategy browser workflow: **passed** after the shared Trading settings changes.
- Frontend TypeScript/Vite build and formatting checks: passed. The existing bundle-size warning remains.
- Backend Ruff checks: passed.
- Pinned Linux Node 24 OpenClaw image: plugin TypeScript build, SDK-generated six-tool manifest and plugin validation passed. Offline plugin factory/effect-guard tests and populated/empty Gateway configuration validation passed with container networking disabled.
- The pre-upgrade local database backup was restored into a separate disposable database successfully and that database was removed. The dump is private under ignored `artifacts/backups/`; its summary is in `artifacts/portfolio-backup-verification.txt`.
- The local Compose preview was rebuilt and upgraded from `0014_strategy_schedules` to `0016_portfolio_chat`, including the existing `0015_autonomous_paper` migration. API and Gateway health pass; existing workers are running. No portfolio or autonomy policies were seeded/enabled, and research/review queues remain empty. The new portfolio endpoint requires owner authentication.

Browser evidence: `artifacts/portfolio-chat-desktop.png`, `artifacts/portfolio-chat-mobile.png`, and `artifacts/portfolio-browser-results.json`. These screenshots and receipts come from disposable fixtures, not the owner's portfolio balances or a real model conversation.

## Remaining scope

Automatic daily opportunity and strategy research, schedules/digests, portfolio-level research budgets, qualification/promotion, automatic reassessment/resume, managed capital transfers, additional broker/asset routes, scalping-compatible native context and full equity-based analytics remain in the implementation plan. No live execution or real model calls were enabled by this verification.
