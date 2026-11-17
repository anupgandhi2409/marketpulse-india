# MarketPulse India

MarketPulse India is a local-first, read-only Indian-market dashboard with a manually generated Morning PDF.

The Streamlit dashboard displays six market indices, Gold, Silver and Crude Oil futures, a 12-sector heatmap, NIFTY 100 gainers and losers, and NIFTY 100 stocks at reported 52-week highs. Economic Times news is included in the Morning PDF only.

## Run locally

Activate the project environment and start the dashboard from the repository root:

```bash
source .venv/bin/activate
streamlit run app/marketpulse/dashboard/app.py
```

Market data refresh occurs after an explicit user action. The Morning PDF collects its own fresh snapshot and is available for download from the dashboard. PDF files are stored locally under `data/reports/`; existing files require explicit replacement.

## Data and limits

- Kotak Neo access is read-only. The application does not place trades.
- Provider changes and retrieval timestamps are shown as received; they do not independently establish that an observation is the last completed trading session.
- Economic Times RSS feeds are requested only as part of explicit Morning PDF generation. The report includes up to 15 linked headlines in each of the Stock Market News, Economic News and Company News categories. News is not shown on the dashboard.
- Market and news sections can be unavailable independently. Missing values and publication times are not inferred.
- Reports and local instrument data remain on the machine. No automatic report scheduling is provided.
