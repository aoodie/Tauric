# Chat research capability correction

Verified locally on 4 October 2026. The XAU/USD weekly refusal exposed stale crypto-only instructions, read-only context and a three-symbol request-tool schema. The platform already supports daily stock/ETF analysis and daily Forex analysis for supported registered pairs.

Every web turn now receives bounded, fresh registry-backed capability facts; general and portfolio context tools expose the same facts. The tool accepts a registered instrument ID, and admission checks its actual native research scope before reuse, allowance charging or queuing. Supported registered stock/ETF research can use the existing owner-bound chat route. Owner identity, active lease, portfolio binding, idempotency, allowances and connection validation remain enforced.

Forex daily requests still use the analysis screen. Chat dispatch and automated portfolio Forex native research remain deferred. Commodities, including XAU/USD, and weekly reports are not implemented. Instructions identify the exact missing asset/scope, offer alternatives as choices, and prohibit silently substituting a daily report or gold ETF. No model call was used to verify generated wording.

## Evidence

- `artifacts/chat-capabilities-tests.log`: 79 backend tests passed, covering chat, owner-bound research, portfolio chat and native Forex. New cases include stock/ETF dispatch, added registry symbols, truthful Forex capabilities, unsupported weekly scope, unknown XAU identity and rejection without charges/jobs.
- `artifacts/chat-capabilities-plugin-tests.log`: actual pinned SDK tool factories and Gateway configuration schema passed offline. Plugin compilation and manifest generation passed.
- Changed Python files passed lint and formatting. API contracts were regenerated. The optional frontend build encountered concurrent unfinished broker UI work (`BrokerExecution.tsx` referenced the then-missing `BrokerRoutes` module); this correction changes no frontend behavior and did not replace the served frontend.
- `artifacts/chat-capabilities-preview-verification.json`: ready local API and chat worker match all five changed source files; the private Gateway plugin and copied workspace instructions match; its actual read-only context confirms daily Forex and stock/ETF chat scope. Zero verification model calls and research requests.

Deployment used two small image layers built from the running backend/Gateway images, containing only the tested chat files. Only API, chat worker and Gateway were recreated; no migration was required. The source changes are also present in the normal build context for subsequent full deployments. The read-only check can be repeated with `python scripts/chat_capabilities_preview_check.py`.
