# Market intelligence

The workstation has a Market intelligence page with Overview, Forex, Stocks & ETFs, Crypto, Commodities, and Rates & bonds tabs. The page follows a shared layout while keeping asset identity, calendars and coverage explicit.

## Using the page

Open Market intelligence from the sidebar. Browse the provider catalogs, search symbols or company names, star instruments to save a personal watchlist, and select a symbol in the performance map or movers table. The default universe includes connected OANDA currency pairs, active tradable Alpaca US listings and active Binance USDT spot pairs. Each page contains at most 24 symbols; prices load for newly displayed symbols. The watchlist is a separate filter. Browsing, price collection and starring a listing do not register it for research or add it to a scanner.

Catalogs are checked when the page opens. **Reload markets** checks them again, reusing catalog checks for five minutes. Provider outages retain the last verified catalog with a stale warning. Catalog receipts older than a day are stale. New US listings retain a neutral US listing classification because the provider catalog does not distinguish stocks from ETFs. **Add to research workspace** verifies current provider availability; the owner explicitly chooses Stock or ETF for US listings. Crypto and Forex retain their verified market identities.

**Reload saved** reads existing observations and source receipts. **Refresh prices & sources** manually fetches daily prices and source metadata for the displayed page, up to 24 symbols. A refresh does not scan, admit research, call a model or submit an order. Policy sources are shared by currency, and identical news batches reuse source checks for five minutes.

The instrument panel links to the exact instrument's daily chart, price receipt, related headlines and saved research. **Ask OpenClaw** prepares an editable draft containing instrument identity, saved evidence and source links. The owner sends it through the existing chat flow.

## Available evidence

| Market | Available now | Remaining coverage |
| --- | --- | --- |
| Overview | Searchable provider catalogs, balanced market browsing, daily observations, watchlist and current eligible scanner candidates | Additional asset classes and cross-asset exposure analysis |
| Forex | OANDA daily prices; primary policy news and date-only meetings for USD, EUR, GBP and JPY | Other policy currencies, complete economic releases, positioning and surprise data |
| Stocks & ETFs | Alpaca daily prices and related headline metadata using the saved connection | Dedicated earnings calendar, estimates, analyst revisions and sector classifications |
| Crypto | Binance spot daily prices; related Alpaca news where matching metadata is available | Funding, open interest, derivatives, liquidations and unlock calendars |
| Commodities | Explicit coverage panel | Dedicated commodity instrument identity and price/source adapters, including XAU/USD |
| Rates & bonds | Explicit coverage panel | Yield curves, named bond instruments, spreads and appropriate price/source adapters |

Commodity and rates tabs do not invent data or relabel ETFs as underlying instruments. Price/source availability depends on the saved connection and provider access. Empty, unavailable and old source checks remain visible.

Changes compare completed daily closes over 1, 5 and 21 trading bars. This is not necessarily 1, 5 or 21 calendar days. Crypto uses UTC days and Forex uses OANDA session calendars. Registered stocks retain regular-session chart evidence. Unregistered US listings use provider-defined split-adjusted daily aggregates with New York midnight boundaries, a shared verified trading calendar and the configured feed; the whole local day must be complete. Their browsing chart is separate from regular-session research evidence. Forming bars are excluded. Missing recent bars, gaps within the recent comparison window, failed checksums and future timestamps suppress comparisons. Older gaps outside that verified window remain disclosed without hiding valid recent returns. Price labels show the daily close and receipt time, rather than claiming a live quote.

The performance map uses equal-sized cells for the displayed symbols. Movers sort within that page; they are not a full-market ranking. Activity compares the latest completed daily volume with the preceding 20 bars. Forex activity is tick volume, stocks use the configured feed's share volume, and crypto uses base volume. These measures should not be compared as a common traded-volume unit.

The research shortlist is separate from movers. It rechecks each current enabled scanner profile's Candidate Gate and ranking, including ignored, expired and already processing candidates. It shows saved detector evidence; it does not infer profitability from a price change.

## Source receipts and storage

Headline and policy evidence retains source URLs, publication/update or event dates, receipt time, instrument/currency bindings and a checksum. News is reported context; matching a headline to a symbol does not establish the cause of its price move. Policy meetings retain their source timezone and date-only precision. Past meetings are excluded from upcoming catalysts.

Alpaca requests contain only bounded metadata; article bodies are not fetched. Source text is evidence, never instructions. Metadata is validated before display. Refresh failures store an unavailable source check. Receipts older than six hours are marked stale; future-dated receipts are unavailable and cannot suppress another refresh.

Migration `0029_market_intelligence` adds owner-scoped immutable source receipts and append-only watchlist revisions. Existing authentication and CSRF protection apply. Watchlist saves check the expected revision and verified catalog or registered identities. Broader watchlists are immutable owner receipts; legacy watchlist revisions remain readable. Alias resolution preserves watched symbols when they are later registered. Catalog prices use separate owner receipts and never become native research inputs. Receipt retrieval checks ownership and integrity. Updating or deleting receipt/watchlist history is denied by database triggers.

## Verification

The wider-catalog correction passed 22 focused tests including the production browser workflow, plus 128 compatibility checks covering instrument discovery, market adapters, connections and research routes. The real connected catalogs contain 68 Forex pairs, 13,191 US listings and 501 crypto pairs at the October 5 check. Catalog size depends on provider availability and can change. Verification of prices outside the nine starters is recorded separately in `artifacts/intelligence-catalog-live.json`.

The compatibility suite passed **150 tests**, including daily-close arithmetic, forming-bar exclusion, partial provider failures, source deduplication, future timestamps, current scanner eligibility, immutable storage, ownership, authentication and existing price/chat/scanner flows. The production frontend build passed.

Browser checks used a disposable database and explicit source fixtures. Eight workflows covered watchlist persistence, filtering, refresh/source inspection, exact chart links, editable chat drafts, Forex catalysts, commodity/rates coverage, wider catalog pagination/search/charts/watchlists and a 390-pixel mobile layout. No browser page errors or horizontal page overflow were observed. These fixtures are not evidence of live provider access.

The private preview backup was restored into an isolated database. Upgrading it preserved all 67 existing tables and added two empty intelligence tables. The coordinated preview rollout verified matching source across ten backend services and matching served frontend assets. Its separate scanner change appended a scanner profile revision and added the candidate research worker; unrelated versioned owner settings were unchanged.

Real source checks collected daily prices for all nine starter symbols and verified their 1/5/21-bar comparisons. All nine news/policy sources were available; the bounded merged view held 68 headlines and 32 upcoming policy meetings at verification time. The manual refresh left the AI research job count unchanged. The research shortlist varies with current saved eligibility and is allowed to be empty. These are point-in-time connection checks, not a guarantee of future provider availability.

Deployment checks are recorded in `artifacts/intelligence-preview-verification.json`, the real refresh in `artifacts/intelligence-live-sources.json`, and final deployed comparisons in `artifacts/intelligence-live-final.json`. The shared backend regression also passed 872 tests, with browser checks run separately.

Useful checks:

```sh
python3 scripts/local.py test tests/test_intelligence.py tests/test_intelligence_candidates.py tests/test_intelligence_catalog.py -q --tb=short
npm --prefix frontend run build
python3 scripts/intelligence_preview_check.py
python3 scripts/intelligence_catalog_live_check.py
```

The browser test requires the production frontend build and an installed Playwright Chromium browser. Upgrade rehearsal creates a private backup and a disposable restored database; it does not migrate the primary database itself. Restore a verified backup to roll back immutable history.
