# Research instrument discovery

Implemented 4 October 2026 as the first stage of the owner's [intelligence and strategy discovery priorities](INTELLIGENCE_DISCOVERY_PLAN.md). This removes the fixed starter catalog as the only way to select research instruments. It does not implement automated strategy-hypothesis generation or expand execution authority.

## Available workflow

Choose **Find more symbols** in the market chart, scanner profile form or portfolio research form. Select **Forex · OANDA** or **Stocks & ETFs · Alpaca**, enter a ticker/pair or name and choose **Search symbols**. Exact symbols appear before related listings. Results are paginated and display catalog observation time, availability, exchange and existing selections.

For Alpaca, select **Stock** or **ETF** before choosing **Add symbol**. This is an explicit owner classification: the provider's US-equity catalog does not establish whether a listing is a company or fund. Classification is retained in the immutable instrument definition. Existing registered symbols cannot be reclassified through this action. ETF daily research omits company fundamentals. OANDA includes currency pairs only; metals, CFDs and other products are excluded. Unsupported/inactive/OTC listings remain visibly unavailable.

An addition rechecks the selected instrument directly with the provider, appends its stable definition and an audit event, and updates the shared selector. The chart selects the added symbol when discovery is opened there. Scanner and portfolio selections still require the owner to select the symbol and save their existing versioned settings. Saving an instrument starts no scan, model request or trade. Scanner and portfolio research universes retain their existing ten-instrument-per-profile/policy bound; multiple scanner profiles can use different subsets. Large scheduled-universe processing is separate work.

The shared PostgreSQL registry supplies charts, scanner choices and portfolio capability lists. Existing instruments, datasets, reports and configuration references are preserved. Provider catalogs are searched without inserting thousands of listings into the registry. The initial nine entries remain until the owner adds selected symbols; merely opening discovery adds nothing.

## Coverage and connection boundaries

OANDA discovery requires a saved token and account ID. Alpaca discovery uses the saved market-data connection and its selected paper/live environment. The implementation calls the official [OANDA account instruments endpoint](https://developer.oanda.com/rest-live-v20/account-ep/) and [Alpaca US assets endpoint](https://docs.alpaca.markets/us/reference/get-v2-assets-1). Only fixed provider HTTPS hosts and GET requests are used. Catalog responses have byte/row bounds, redirects are rejected and failures are redacted. Catalog observations are cached for up to five minutes using a credential/account/environment fingerprint; adding a symbol always rechecks current availability. A changed saved connection revision rejects an addition until the owner searches again.

Catalog availability is separate from historical data coverage, feed entitlement and research or execution eligibility. All pairs using the currently supported EUR/USD/GBP/JPY currencies can use the daily Forex AI adapter; other registered pairs expose charts and deterministic screening/replay while daily AI research remains unavailable. Additional central-bank/economic adapters are needed to extend that coverage. Intraday/scalping qualification remains a separate gate.

Migration `0027_instrument_discovery` follows `0026_broker_review` and grants the application INSERT permission on the existing instrument table. Its update/delete/truncate protections remain in place. No new tables, seeded research universes or broker policies are required. Deploy matching source and migrate before starting services. Preserve existing credentials and the encryption key with backups.

## Verification

- Final affected suite: **155 passed**, including the built browser test with six workflows. Tests cover authentication/CSRF, provider and identifier validation, bounded pagination, exact provider binding, unsupported instruments, connection changes, cache isolation, revalidation, immutable/idempotent registration, ETF scope and persisted scanner/portfolio universes. Evidence: `artifacts/instrument-discovery-final-tests.log`.
- The browser uses explicit offline provider fixtures and disposable PostgreSQL. It exercises chart selection, stock/ETF classification, extra FX pairs, unavailable/empty results, reload persistence, disabled automation and desktop/mobile rendering. It starts no AI job. Evidence: `artifacts/instrument-discovery-browser-results.json` and the desktop/mobile screenshots.
- Frontend production build, scoped formatting and lint passed. Evidence: `artifacts/instrument-discovery-build.log`.
- A private primary backup restored and migrated successfully in an isolated database. All **61** pre-existing tables retained their original column values and canonical row checksums, including the nine original instruments. Existing immutable instrument guards remained installed. Evidence: `artifacts/instrument-discovery-backup-verification.json`. This rehearsal did not migrate the primary database.
- Live read-only discovery returned **68 OANDA currency pairs** and **14,388 Alpaca US listings**, with EUR/GBP and NVDA matches. Counts describe catalog rows, including listings whose eligibility is explicitly limited; they are not certification that every symbol has usable market/research data. No instrument, AI job or broker order was created by this check. Evidence: `artifacts/instrument-discovery-live.json`.

## Local deployment, 4 October 2026

The shared build is published locally at `http://127.0.0.1:8080`, with schema `0027_instrument_discovery` and successful HTTP readiness. All six discovery-related source modules match across all eight backend services. The served browser bundle includes the picker, and unauthenticated catalog access is rejected. Read-only calls from the deployed API returned the same 68 OANDA pairs and 14,388 Alpaca listings. No registry insertion, AI job or broker order was requested by these verification calls. Evidence: `artifacts/instrument-discovery-preview-verification.json` and `instrument-discovery-preview.log`.

The shared release additionally passed 766 non-browser checks and 16 browser tests before deployment; those checks include the concurrently developed broker-review route and do not certify unattended trading. The final discovery browser rerun verifies that pressing Enter inside scanner discovery searches without submitting the surrounding settings form; evidence is in `artifacts/instrument-discovery-final-browser.log`. Automated intelligence ingestion, scheduled large-universe screening and strategy proposal generation remain the next discovery stages.
