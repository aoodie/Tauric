# Open commodity and timeframe research

The owner requested open research for any commodity and timeframe on 9 October 2026. The chat research scope is open: instrument registration and a native daily report are not prerequisites. Exact price coverage remains dependent on a provider's catalog, account entitlement, retained history and data quality.

OpenClaw's `tauric_open_research` tool has four read-only operations:

- `search`: public source discovery, with links and explicitly unverified snippets.
- `read`: bounded original public HTTPS source text, receipt time, redirect provenance and a content checksum. Prefer exchanges, official statistics, producer reports and original market sources. Access failures remain explicit.
- `discover`: available OANDA account instruments and public Yahoo Finance symbols. Identifies metals, CFDs, futures, ETFs and other products without silently substituting them.
- `prices`: timestamped price history for an exact discovered symbol and selected provider, with optional historical start/end timestamps. Returns closed and forming bars separately, freshness, source, product metadata, warnings and a checksum.

`tauric_market_data` also accepts unregistered OANDA account products, including silver, other metals and commodity CFDs. Existing registered standard chart requests retain their adapters. Commodity discovery is separate from adding instruments to the execution registry; the latter still has its existing asset contracts.

Price timeframes use a positive number followed by `s`, `m`, `h`, `d`, `w`, `mo` or `y`. OANDA supports its native 5-second through monthly granularities. Public chart feeds support available minute through monthly history. Other requested intervals are aggregated from complete underlying bars where possible. Partial, gapped or insufficient groups are excluded and counted. OANDA daily/weekly/monthly endpoints use New York calendar alignment and daylight saving, including the prior-evening session that begins a month; custom intraday intervals use UTC epoch alignment. Public calendar aggregates use the reported exchange timezone; endpoints remain estimates, with their limitation explicit. Providers impose retention, bar count and access limits; requests outside exact candle coverage can still receive source research.

Spot, commodity CFDs, individual futures, continuous futures and ETFs are distinct markets. Continuous futures can roll and public prices can be delayed. Some commodity futures have negative historical prices. Invalid OHLC bars, including settlements outside the reported high/low, are quarantined and counted instead of repaired or silently treated as verified bars.

Every tool call checks the owner conversation and active lease before and after collection. Immutable audit receipts retain collected evidence and checksums; the chat answer supplies the sourced synthesis. Public source connections validate all resolved IPs and pin the actual socket destination, validate every redirect, retain TLS hostname verification, bound responses/time and never send broker headers. External text and metadata are evidence, never instructions or authorization.

Open research creates no native graph report, strategy qualification, account change, governance approval or order. Existing native daily report and financial controls remain separate.

## Research screen

The workspace chart's **Research** button opens research regardless of native report eligibility. **Research any pair or market** accepts an unsaved pair or product and a timeframe such as `15m`, `7m`, `1w` or `1mo`. **Ask OpenClaw** opens a chat draft retaining that exact request; sending it uses the existing open research tools. It does not require the native worker or a built-in daily report. Provider access and retained history still determine exact candle availability; source research can continue when candles are unavailable.

The built-in daily report remains a separate option for eligible saved instruments. Its model, evidence and worker checks still apply specifically to that report. No trading or qualification gate is removed.

Deployed on 9 October 2026 at 19:57 BST as web source supplement `sha256:047639cb1583718fc3da28fa3e440a6b2a81d774434e0e0fae1d5503463d273e`, built from deployed frontend revision `18b3d34` with only five research-screen source files changed. All 68 installed static files match the tested release, all 15 services are healthy, and the 14 backend containers and private configuration were unchanged. The previous web files were backed up and independently restored for verification. No database migration or financial-route change occurred.

Verification: 67 scoped research and browser tests passed, followed by a browser check of the isolated release itself. The tests cover an instrument without native eligibility, unsaved silver and gold, futures identity, custom/weekly/monthly timeframes, and mobile layout. Records are in `artifacts/open-research-browser-results.json`, `artifacts/native-independent-release-browser.log`, and `artifacts/native-independent-research-deployment.json`. Public HTTPS assets and readiness were verified. Saved owner credentials were rejected during the production sign-in check, and no authenticated browser session was available, so the signed-in production flow remains unverified; no credentials were changed and no research or broker request was submitted by that check.

## Implementation and verification status

Source implementation and local tests are recorded in `artifacts/open-commodity-research-tests.log`. Pinned Gateway/plugin checks are recorded in `artifacts/open-commodity-openclaw-checks.log`. Production installation and real connected evidence must be recorded separately in `artifacts/open-commodity-deployment.json` and `artifacts/open-commodity-live-verification.json`; implementation alone is not proof of deployment or a successful model-generated answer.
