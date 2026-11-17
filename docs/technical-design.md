# MarketPulse India Technical Design

## Runtime structure

The Python application is under `app/marketpulse`. Streamlit presents the local dashboard and manual Morning PDF controls. Data services can be used independently of Streamlit. Kotak Neo access is read-only at the application boundary.

## Market data

- `data/kotak_neo.py` wraps the installed SDK for instrument-master retrieval, instrument search and quote requests.
- Instrument parser, store and service modules validate local masters and resolve exact contracts.
- `data/index_quote_service.py` validates the six index identities and parses index value, provider change fields and optional quote timestamps.
- `data/sector_performance_service.py` validates the twelve sector records.
- `data/market_movers_service.py` loads the local NIFTY 100 universe and returns bounded quote batches used by both movers and 52-week-high services.
- Gold, Silver and Crude Oil services select eligible futures and validate quote identities and numeric values.

The report collector shares NIFTY 100 quote retrieval between movers and 52-week highs. Each report section validates its own coverage and data. A section failure does not prevent unrelated data collection.

## Economic Times news

`data/news_sources/et_rss.py` parses bounded RSS XML into the news collection models. `data/news_sources/et_acquirer.py` defines the three fixed Economic Times feeds and handles verified HTTPS retrieval, response limits, strict redirects and sanitized failures. Each feed is requested independently without retries or article-page requests.

`data/market_news_window.py` builds a Morning calendar window from the previous day at 18:00 IST to the explicit report timestamp. The collector selects dated items inside that interval, removes duplicate article URLs within a section, preserves source order and maps each feed to its own `ReportArticleSection`. Each section is capped at 15 items. Empty, unavailable and unconfigured results remain distinct; missing publication times are not inferred.

The PDF renderer shows three news sections at the end of the document. Each item has a small bullet, a linked headline and the publisher/publication-time line. Descriptions and technical source, retrieval and coverage details are not rendered. News is not displayed or collected by dashboard refresh.

## Report pipeline

```text
Market-data services + optional ET RSS acquisition
                         ↓
                 ReportDataCollector
                         ↓
                  ReportSnapshot
                         ↓
                 ReportPdfRenderer
                         ↓
                  ReportFileStore
```

`reports/collector.py` obtains a fresh market snapshot and optional ET news during explicit PDF generation; it does not reuse dashboard cache. The report generation timestamp is recorded in IST, while original data retrieval and article publication timestamps remain attached to their records.

`reports/snapshot.py` defines the immutable `ReportSnapshot`, typed market `ReportSection` values, coverage metadata, and three optional news listing sections. Market sections use `AVAILABLE`, `CACHED` and `UNAVAILABLE`; news sections distinguish `AVAILABLE`, `UNAVAILABLE` and `NOT_CONFIGURED`. Snapshot validation checks required identities, coverage and aware timestamps. It does not infer an exchange trading-session date.

`reports/generation.py` orchestrates collection, PDF byte generation and publication. `reports/pdf_renderer.py` renders the order: six indices; Gold, Silver and Crude Oil; Last Trading Day Data with sectors, movers and 52-week highs; then Stock Market News, Economic News and Company News. The first heading doubles as the stocks category heading. The PDF uses an ivory page background, compact rounded quote cards with regular-weight Change lines, and rounded sector tiles with sign-based fills (`#A6CF98`, `#FF8080`, and a neutral ivory-compatible fill). Sector names, percentages and index values occupy distinct lines. Gainers and losers begin on page two with their tables kept with the heading. News links use validated original ET article URLs.

`reports/file_store.py` stages output locally and atomically publishes `data/reports/marketpulse_morning_<YYYY-MM-DD>.pdf`. Existing files are protected unless replacement is explicitly requested.

## Streamlit behavior

`dashboard/app.py` begins with session-state data and creates quote services only after **Refresh Market Data**. It displays indices, commodities and Last Trading Day Data in that order, followed by manual Morning PDF controls. Market index and commodity cards use a shared responsive HTML grid and shared typography. Heatmap tiles use one responsive CSS grid with equal row and column gaps. Dashboard ranking tables use Pandas Styler CSS to center every header and cell while retaining change-sign coloring; the 52-week-high table uses the same centering helper. Financial table values are preformatted to two decimals and currency labels use `₹`. News is neither displayed nor collected during dashboard refresh. PDF generation performs its own collection. The saved PDF bytes are held in session state for the download action, and user-visible failures use fixed safe messages.

## Security and intentional limitations

- No trading APIs are called.
- Credentials, authorization headers, response bodies and raw SDK exceptions are not included in user-visible report output.
- ET access is restricted to fixed feed endpoints with TLS verification, bounded responses and redirect validation.
- Quote changes and retrieval times are provider observations. The application does not independently establish prior-session identity or manufacture missing values.
- The ET news window uses calendar-day boundaries and does not account for weekends or holidays. RSS results do not establish complete publisher coverage.
- User-generated reports, local instrument masters, constituent data, credentials and `.env` files are local data and are not part of code cleanup.

## Verification

The `unittest` suite uses synthetic data and injected provider/acquirer fakes. It covers parser and quote validation, instrument resolution, NIFTY 100 coverage, failure isolation, ET RSS security, snapshot validation, PDF rendering and storage, and dashboard behavior without live provider calls.
