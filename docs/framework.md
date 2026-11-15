# MarketPulse India – Technical Framework Guide

## 1. Project purpose and current scope

MarketPulse India is a local-first market dashboard and reporting application for Indian markets. Its current product surface is a read-only Streamlit dashboard backed by Kotak Neo REST market data. The next milestone is to compose the validated data into morning and evening PDF market reports.

The dashboard currently covers six market indices, twelve NSE sector indices, NIFTY 100 gainers and losers, NIFTY 100 stocks at their reported 52-week highs, MCX Gold, Silver and Crude Oil futures, and USD/INR currency futures. It also shows an indicative USD equivalent for Crude Oil using the USD/INR futures price. Automated trading, scheduled refresh, FII/DII, and PDF reporting are not implemented.

## 2. Technology stack

| Area | Implemented technology |
| --- | --- |
| Runtime | Python 3.13 (`>=3.13,<3.14`) |
| Market-data provider | Kotak Neo SDK 3.0.13, wrapped by `KotakNeoProvider` |
| Dashboard | Streamlit |
| Instrument files | Standard-library CSV parsing and local CSV storage |
| Numeric market values | `Decimal` |
| Tests | Python `unittest`; `pytest` is an optional development dependency |

Project dependencies are declared in `pyproject.toml`. The current runtime dependencies include `kotakneoapi==3.0.13`, `certifi`, and `streamlit`.

## 3. Application architecture

The code separates provider access, local instrument data, validation and source-neutral quote models, dashboard orchestration, and rendering:

```text
KotakNeoProvider ──▶ quote and market-data services ──▶ dashboard market-data models
       ▲                         ▲                                  │
       │                         │                                  ▼
Settings / credentials    instrument services                Streamlit rendering
                              ▲
                              │
                    local segment CSV files
```

`app/marketpulse/data/kotak_neo.py` is a thin read-only SDK adapter. Services resolve instruments, validate response identities and values, and return models that do not expose SDK response objects to the dashboard. `app/marketpulse/dashboard/market_data.py` builds the services, coordinates refreshes, retains successful snapshots, and prepares validated presentation values. `app/marketpulse/dashboard/app.py` renders the Streamlit interface.

## 4. Current market-data capabilities

- **Indices:** six verified REST indices: NIFTY 50, NIFTY BANK, NIFTY IT, NIFTY MIDCAP 100, NIFTY SMALLCAP 100, and SENSEX.
- **Sector performance:** one REST batch for twelve ordered NSE sector indices, with index values and percentage changes. The observed provider `lstup_time` placeholder is not used as market time.
- **NIFTY 100 market movers:** the top ten gainers and losers from validated `per_change` values, with full-universe coverage required.
- **NIFTY 100 52-week highs:** up to ten stocks for which LTP is greater than or equal to Kotak's reported `year_high`. A complete scan may return zero stocks; stocks merely near the high are not included.
- **Futures:** dynamically selected active standard MCX GOLD, SILVER and CRUDEOIL futures, plus USDINR currency futures. Contracts are selected from local master metadata rather than fixed tokens.
- **Indicative Crude Oil conversion:** Crude Oil INR per barrel divided by USD/INR futures INR per USD, using `Decimal` and half-up rounding to two decimal places. It is an indicative USD equivalent, not a WTI or Brent quote.
- **Equity quote backend:** reusable NSE cash equity quote models, instrument lookup, and `NSEEquityQuoteService` remain available. The original fixed eight-stock watchlist is not part of the active dashboard or planned daily reports; its reusable service logic and tests remain in the repository.

FII/FPI and DII institutional activity integration is deferred. No FII/DII figures are included in the dashboard or report scope until a suitable source, definitions, and permitted retrieval method are established.

## 5. Local instrument and constituent data

`InstrumentMasterAcquirer`, `InstrumentMasterStore`, `InstrumentMasterParser`, and `InstrumentMasterService` provide segment-specific acquisition, validation, storage, and lookup. Application-managed instrument files are stored under `data/instruments/` as `<exchange_segment>.csv`; the store validates input and atomically replaces a saved file. Acquisition is explicit and is not a dashboard refresh action.

The NIFTY 100 services use the local constituent file `data/constituents/ind_nifty100list.csv` and the local NSE Cash master `data/instruments/nse_cm.csv`. MCX futures use `data/instruments/mcx_fo.csv`, and USDINR futures use `data/instruments/cde_fo.csv`. These data files are local and Git-ignored; they are not application source code. NIFTY 100 symbols are validated against exact NSE Cash EQ instruments and their trading symbols, series, ISINs, and unique tokens.

## 6. Dashboard refresh and cached results

The dashboard does not fetch market data during ordinary Streamlit reruns. The user starts retrieval with **Refresh Market Data**. Successful results are retained in Streamlit session state with their original retrieval times.

The refresh orchestrator handles indices, commodities and currency, sectors, and the NIFTY 100 panels independently. A dataset failure produces a sanitized unavailable/error state and retains its previous successful snapshot, marked as cached. Other datasets can still refresh. The NIFTY 100 market-movers and 52-week-high panels share the same four validated batches of 25 stock quotes in a refresh, avoiding a second set of requests. If that shared constituent resolution or quote acquisition fails, both NIFTY 100 panels remain on their previous cached snapshots or show unavailable.

Dashboard retrieval timestamps are converted for display in India Standard Time and labeled **Retrieved at**. They are not represented as exchange trading timestamps. The dashboard states that market-data freshness is not independently verified.

## 7. Read-only access and credential handling

The application uses Kotak Neo's consumer-key-based REST market-data path through `KotakNeoProvider`. Configuration is read from `KOTAK_CONSUMER_KEY` and `KOTAK_ENVIRONMENT`; credentials are not part of source files. The application adapter does not expose order placement, order management, trade-enabled login, TOTP/MPIN, WebSocket/SFeed, or trading operations. Live market-data access is read-only.

Provider responses are validated at service boundaries. Dashboard-facing errors are mapped to sanitized messages; credentials, raw quote payloads, and provider error details should not be displayed or logged.

## 8. Development status and next milestone

The Streamlit dashboard and its current market-data panels are implemented. The latest reported local validation is 240 passing `unittest` tests, `git diff --check` passed, and the Streamlit application import passed. A separate full browser verification reported 10/10 checks passed. These are the reported results for the completed dashboard milestone, not a claim that an exchange timestamp or data freshness is guaranteed.

**Next milestone: PDF market-report generation.** The report layer should consume validated service results and retrieval/coverage metadata through a source-neutral report model. It should not call provider APIs from rendering code. Morning and evening scheduling, persistence/history, and any automation remain future work. The fixed eight-stock watchlist is excluded from these planned daily reports. FII/DII remains deferred.

## 9. Data and market interpretation limitations

- Kotak quote timestamps may be placeholders or have unverified meaning. Retrieval time is recorded separately and must not be presented as exchange market time.
- USD/INR is a currency futures quote, not a spot rate or RBI reference rate.
- The Crude Oil conversion is an approximate cross-currency equivalent using that futures quote; it is not WTI or Brent.
- A contract's stored expiry timestamp is used for eligibility and selection. It is not automatically an official calendar expiry date.
- The 52-week-high scan uses `LTP >= reported year_high`; it may return an empty list when no stock qualifies.
- Instrument-master and constituent files are local snapshots. Their refresh cadence and freshness are not automated by the dashboard.
- FII/DII, PDF output, and scheduled report generation are not implemented.

## 10. Local development workflow

Use the repository's Python 3.13 virtual environment. Configure Kotak settings locally and keep credentials out of tracked files. Run the full unit suite with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Launch the dashboard from the project root with:

```bash
.venv/bin/streamlit run app/marketpulse/dashboard/app.py
```

Tests use mocks and local fixtures; routine test runs do not make live Kotak requests.
