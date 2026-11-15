# MarketPulse India – Technical Design

## Status labels

- **Implemented:** present in the application and covered by local tests.
- **Live-verified:** the relevant Kotak read-only REST behavior has also been manually exercised.
- **Deferred:** intentionally outside the current completed milestone.
- **Future plan:** intended follow-up work, not implemented yet.

## 1. Architecture overview

MarketPulse India is a local-first Python application with a Streamlit dashboard. Kotak Neo REST calls are isolated behind `KotakNeoProvider`; service classes resolve instruments and validate provider responses; immutable source-neutral models carry validated values; dashboard orchestration controls retrieval, caching and error state; Streamlit code renders those results.

```text
Settings ──▶ KotakNeoProvider ──▶ data services ──▶ validated models
                                      ▲                  │
                                      │                  ▼
                      instrument store/parser     dashboard orchestration
                                                         │
                                                         ▼
                                                   Streamlit rendering
                                                         │
                                              future report-model boundary
```

Business rules are outside Streamlit widgets. The dashboard does not call the SDK during rendering or on ordinary reruns. PDF/report composition, persistence of report history, and scheduling are not present; future report code should consume service results and their coverage/retrieval metadata rather than access the provider directly.

## 2. Configuration and Kotak provider

**Implemented:** `config/settings.py` provides frozen settings, project/data paths, and environment-backed `KOTAK_CONSUMER_KEY` and `KOTAK_ENVIRONMENT`. `data/kotak_neo.py` wraps the installed `kotakneoapi==3.0.13` SDK and exposes the application's read-only scrip-master, instrument-search, quote, and historical-data adapter methods. Services validate returned shapes and values. The application does not use trade login, TOTP/MPIN, WebSocket/SFeed, order or trading operations.

Provider calls are mockable through injected clients. Tests do not require credentials or live requests. Dashboard-facing failure messages are sanitized rather than displaying provider payloads or sensitive exception text.

## 3. Instrument-master acquisition and lookup

**Implemented:**

- `data/instrument_acquirer.py` obtains a segment-specific file location from the provider, downloads it through the bounded HTTPS downloader with TLS verification, and saves it through the store.
- `data/instrument_store.py` stores segment snapshots as `data/instruments/<segment>.csv`, validates CSV structure, and uses atomic replacement.
- `data/instrument_parser.py` validates headers/rows and maps known Kotak fields to the source-neutral `Instrument` model.
- `data/instrument_service.py` loads parsed instruments and supports exact symbol/trading-symbol lookup and quality reporting.
- `data/instrument_quality.py` reports structure and field-quality observations without rewriting or silently rejecting unusual source values.

The master acquisition flow is explicit. It is not run by the dashboard's refresh button. Local files such as `nse_cm.csv`, `mcx_fo.csv`, and `cde_fo.csv` are ignored data snapshots, not tracked application source. Their freshness is not automatically established.

The NIFTY 100 universe is read from `data/constituents/ind_nifty100list.csv`. Market-movers constituent resolution validates the CSV records and resolves exact symbol, EQ series, expected `-EQ` trading symbol, ISIN, cash segment, and unique token against the local NSE Cash master. A missing or malformed file or ambiguous resolution fails closed.

## 4. Quote models and services

### NSE cash equity quotes

`data/quote.py` defines the immutable `Quote`; `data/quote_service.py` implements `NSEEquityQuoteService`. Single-symbol and batch lookups use `InstrumentMasterService` and `nse_cm`. Batch requests resolve the master once and use one `quote_type="all"` provider request. This backend remains reusable and tested, although the original fixed eight-stock watchlist has been removed from active dashboard and planned daily-report scope. Its service implementation and tests remain in the repository.

Some equity response fields are intentionally unavailable in the normalized model: previous close and cumulative session volume are not inferred from unverified `ohlc.close` or `last_volume` semantics.

### Indices

`data/index_quote.py` and `data/index_quote_service.py` map supported display names to verified case-sensitive Kotak identifiers and exchanges. One request can retrieve the requested indices. Identity is matched by identifier and exchange; LTP is parsed as `Decimal`; results follow requested order.

The six dashboard indices are NIFTY 50, NIFTY BANK, NIFTY IT, NIFTY MIDCAP 100, NIFTY SMALLCAP 100, and SENSEX. The service has been live-verified for this set. It does not derive change or volume values.

### Sector performance

`data/sector_performance.py` and `data/sector_performance_service.py` model and retrieve twelve ordered NSE sectors in one `quote_type="all"` REST batch. The service matches exact `exchange_token` plus `exchange`, not provider-formatted `display_symbol`; it requires all twelve records and finite index-value and percentage-change decimals. It preserves positive, negative, and zero changes and returns defined display order.

Kotak's observed `lstup_time` value was a 1900 placeholder. It is discarded. The service records its own retrieval time and does not claim that it is the exchange's trading timestamp.

### NIFTY 100 market movers and 52-week highs

`data/market_movers_service.py` defines the NIFTY 50 and NIFTY 100 universe metadata and shared constituent resolution and quote batching. Each provider request is capped at 25 NSE Cash instruments. The NIFTY 100 requires exactly 100 validated constituents and uses four requests. Each response is checked for exact expected token coverage, uniqueness, exchange and trading identity, and valid finite quote values. Partial results do not produce a ranking.

`analytics/market_movers.py` provides provider-independent deterministic ranking. Gainers are ordered by percentage change descending, losers ascending, with deterministic symbol tie-breaking. The result includes universe, expected and validated counts, full-coverage status, retrieval time, and validated quote records.

`data/year_high_service.py` applies `LTP >= year_high`, validates a complete 100-stock quote set, sorts qualifiers alphabetically, and returns at most ten. It may correctly return zero stocks. It does not fill the list with stocks merely near their high. The model does not carry percentage change, so the dashboard does not display a percentage for this panel.

The dashboard obtains the four validated quote batches once and passes the same batches to both services. This keeps market movers and the 52-week scan aligned to the same fetched snapshot and avoids eight instead of four REST quote calls. If shared acquisition fails, both NIFTY 100 sections retain their prior cached snapshots or show unavailable; other dashboard datasets continue independently.

Both services have been live-verified with Kotak market data. Provider/data failures and incomplete coverage are surfaced through sanitized dashboard states.

### MCX Gold, Silver and Crude Oil futures

`data/commodity_futures_quote.py` provides the common source-neutral `CommodityFuturesQuote` model. `data/commodity_futures_quote_service.py` shares MCX CSV normalization, contract selection, quote identity checks, and finite `Decimal` parsing. The Gold, Silver, and Crude Oil service classes configure the standard commodity symbol and verified quotation unit:

| Contract | Selection | Quotation unit |
| --- | --- | --- |
| Standard MCX GOLD futures | `mcx_fo`, `pSymbolName=GOLD`, `pInstType=FUTCOM` | INR per 10 grams |
| Standard MCX SILVER futures | `mcx_fo`, `pSymbolName=SILVER`, `pInstType=FUTCOM` | INR per kg |
| Standard MCX CRUDEOIL futures | `mcx_fo`, `pSymbolName=CRUDEOIL`, `pInstType=FUTCOM` | INR per barrel |

CSV header whitespace is trimmed. The valid futures `pOptionType=XX` is retained. `lExpiryDate` is interpreted using the verified MCX Unix-seconds representation; the nearest eligible unexpired contract is dynamically selected and one `quote_type="all"` request is made. The timestamp used for selection is retained in the model. It must not be automatically presented as the official contract calendar expiry date.

The services require matching returned token and `mcx_fo` exchange, finite positive LTP, and finite change and percentage change. Optional OHLC open/high/low are parsed when valid. `ohlc.close` is not treated as prior-session close. The observed 1900 placeholder `lstup_time` is not used as a market timestamp. Gold, Silver, and Crude Oil quote paths have passed controlled live verification.

### USD/INR currency futures

`data/currency_futures_quote.py` defines a source-neutral currency futures model; `data/usdinr_futures_quote_service.py` selects the nearest eligible USDINR `FUTCUR` contract from the local `cde_fo` master and makes one REST quote request. It applies the verified cde_fo expiry conversion, validates identity and finite decimal prices/changes, and records expiry and retrieval timestamps separately. The quotation is INR per USD and is a USD/INR currency futures price, not a spot exchange rate or RBI reference rate. The provider's observed weekend timestamp is not treated as proof of current market freshness. The quote path has passed controlled live verification.

### Indicative Crude Oil USD equivalent

`analytics/currency_conversion.py` computes `Crude Oil INR per barrel / USDINR futures INR per USD` using `Decimal`, rounded to two decimal places with `ROUND_HALF_UP`. Dashboard formatting uses already-loaded session-state quotes and does not make another provider request. Both inputs must be positive finite values. The displayed label identifies the value as an indicative USD/barrel equivalent and names currency futures as the conversion basis. It is not a WTI or Brent price, and freshness is not independently verified.

## 5. Dashboard orchestration, caching, and failure handling

`dashboard/market_data.py` contains the `DashboardMarketData` snapshot, service construction, refresh orchestration, validation, presentation models, and safe error mapping. `dashboard/app.py` defines the Streamlit layout and presentation.

On normal Streamlit reruns, session state is rendered without creating provider services or making requests. **Refresh Market Data** creates the services and refreshes indices, Gold, Silver, USD/INR, Crude Oil, sectors, market movers, and 52-week highs. Successful values replace their respective session-state snapshot. A failed dataset keeps its previous successful value and original retrieval time; UI labels identify cached data. Failures in independent sections do not prevent others from updating. NIFTY 100 movers/highs share their acquisition and quote batches as described above.

The dashboard includes six index rows, four commodity/currency cards, a twelve-tile sector heatmap, NIFTY 100 gainers/losers tables, and the NIFTY 100 52-week-high results. Tiles and tables show signed values as well as color; unavailable or incomplete snapshots are not presented as complete. The original fixed eight-stock watchlist section and its refresh request are absent. No PDF composition code currently includes that watchlist.

Retrieval times are retained separately from provider timestamp fields and rendered in `Asia/Kolkata` as **Retrieved at**. Kotak timestamps that are placeholders or have unverified meaning are not presented as exchange times. The dashboard discloses that market-data freshness is not independently verified.

## 6. Tests and verification status

Tests use Python `unittest`, fixtures, and mocks for provider and service behavior. They cover instrument parsing/acquisition/storage, equity and index quote validation, NIFTY 50/100 coverage and batching, sector data, futures selection and quote validation, currency conversion, dashboard formatting, refresh/caching, independent failures, and safe unavailable states. The standard test command is:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The latest reported milestone verification is:

- Full `unittest` suite: 240 passed.
- `git diff --check`: passed.
- Streamlit application import: passed.
- Full browser verification: 10/10 checks passed.

These are the reported checks for the completed dashboard milestone. This documentation-only update does not rerun them and does not claim additional validation.

## 7. Security and operational boundaries

- Keep `KOTAK_CONSUMER_KEY` outside source control and out of logs, dashboard output, and reports.
- Keep provider access behind `KotakNeoProvider` and service validation; do not pass complete raw quote payloads into presentation code.
- The application is read-only. No orders, trading, trade-enabled login, TOTP/MPIN, or WebSocket/SFeed operations belong in this integration.
- There are no automatic retries, background schedules, or dashboard-triggered instrument-master downloads.
- Local constituent and instrument-master snapshots have no dashboard-managed refresh cadence or freshness guarantee.

## 8. Report integration boundary and roadmap

**Implemented boundary:** quote services return validated models with source, retrieval time, instrument identity and, where applicable, universe coverage. Dashboard orchestration consumes those models. The fixed initial eight-stock watchlist is excluded from active dashboard and future daily-report scope; its reusable backend service and tests remain.

**Next milestone — PDF market reports:** introduce a source-neutral report model and composition layer that consumes service results, coverage status, and retrieval metadata. PDF rendering should read the composed model and must not make provider requests. Morning/evening scheduling and report history can follow after report composition and freshness policy are defined.

**Deferred:** FII/FPI and DII activity. No institutional-flow dataset is currently integrated. It must not be approximated by combining sources with different reporting periods or provisional/final definitions.

## 9. Explicit limitations

1. Provider timestamps can be placeholders or lack verified semantics; retrieval time is not an exchange timestamp.
2. USD/INR is a currency futures quote, not spot or RBI reference data.
3. The indicative Crude Oil USD equivalent is not a WTI or Brent quote and inherits the freshness limitations of both source prices.
4. Stored futures expiry timestamps support eligibility/selection but are not automatically official expiry calendar dates.
5. NIFTY 100 52-week-high results require full-universe quote coverage and use `LTP >= reported year_high`; a complete result can contain zero stocks.
6. The dashboard does not establish freshness guarantees for local constituent or instrument-master files.
7. FII/DII, PDF generation, report scheduling, and report history are deferred or future work.
