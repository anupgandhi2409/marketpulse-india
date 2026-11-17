# MarketPulse India Framework

## Product scope

MarketPulse India is a local-first, read-only Indian-market dashboard with a manually generated Morning PDF. It displays six market indices, Gold, Silver and Crude Oil futures, a 12-sector heatmap, NIFTY 100 gainers and losers, and NIFTY 100 stocks at reported 52-week highs. Economic Times news is included in the Morning PDF only.

## Streamlit dashboard

The dashboard displays, in order:

1. Major Market Indices: NIFTY 50, BANK NIFTY, NIFTY IT, NIFTY MIDCAP 100, NIFTY SMALLCAP 100 and SENSEX.
2. Commodities: Gold, Silver and Crude Oil.
3. Last Trading Day Data: Sector Performance Heatmap, NIFTY 100 Top Gainers and Losers, and NIFTY 100 Stocks at 52-Week High.
4. Manual Generate / Download Morning PDF controls.

Market data is requested only after **Refresh Market Data** is selected. Index and commodity cards share a compact ivory card style, consistent padding and regular-weight Change lines. Changes use two decimal places and sign colors; zero and unavailable values use neutral text. Retrieval timestamps remain distinct from exchange timestamps. The dashboard does not display news.

The 12-sector heatmap uses rounded, filled tiles in the service's fixed order. Positive tiles use `#A6CF98`, negative tiles use `#FF8080`, and zero tiles use a neutral ivory-compatible fill; dark text shows the sector name, signed change and index value. The grid wraps responsively with equal horizontal and vertical gaps. Movers and 52-week-high tables center every header and cell, format financial values to two decimals, and use `₹` on monetary labels.

## Morning PDF

Selecting **Generate PDF Report** collects a fresh snapshot and creates a PDF titled **Morning Market Pulse**. Its order is indices, commodities, Last Trading Day Data (sector heatmap, gainers and losers, 52-week highs), then Stock Market News, Economic News and Company News. The Stock Market News heading introduces the stocks feed without a duplicate adjacent title. Each news section contains up to 15 linked headlines with publisher and publication-time metadata.

Index and commodity cards use a shared compact layout with regular-weight Change lines, sign colors, and two-decimal financial formatting. Sector tiles are rounded, use the dashboard's sign-based colors and dark text, and keep each sector name, percentage and index value on distinct lines. Ranking and 52-week-high tables center all headers and cells; currency labels use `₹` and a Unicode-capable font.

ReportLab creates the PDF. Files are stored under `data/reports/` with a Morning report date. Existing files are protected unless **Replace existing report** is selected. The generated file and snapshot are retained in Streamlit session state so downloading does not repeat collection.

Report generation performs its own market and news collection; it does not reuse dashboard session data. Failures are isolated by section and represented without fabricating values. ET collection uses three fixed RSS feeds, filters by the Morning calendar window, and does not fetch linked article pages. News is not rendered in the dashboard.

## Data sources and limitations

- Kotak Neo supplies read-only index, equity and futures quote data. The application does not perform trading operations.
- Local instrument masters and the NIFTY 100 constituent list support contract and constituent lookup.
- NIFTY 100 movers and 52-week highs share validated quote batches.
- Economic Times RSS feeds are retrieved over verified HTTPS with bounded response sizes, strict redirects and no automatic retries.
- Provider-reported changes and timestamps are shown as received. They do not independently prove that an observation is the last completed trading session. The application does not infer a market-session date or manufacture missing values.
- News is filtered from the previous calendar day at 18:00 IST through the report-generation timestamp. The window uses calendar dates and does not adjust for weekends or holidays. Missing publication times are not invented.
- RSS item counts do not establish complete publisher coverage.
