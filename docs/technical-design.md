# MarketPulse India – Technical Design

## Status labels

- **Confirmed:** verified from the existing project or SDK inspection.
- **Implemented:** actually present in the repository.
- **Proposed:** intended design, not implemented yet.
- **Open Question:** requires a future decision or verification.

## 1. System architecture overview

**Proposed:** MarketPulse India will follow a local-first, layered design. Provider adapters retrieve data, processing validates and normalizes it, analytics derives report-ready measures, and dashboard/report components present the results. Configuration and storage paths are shared through a small settings layer.

**Implemented:** the current codebase remains a foundation rather than an end-to-end market-intelligence application. The instrument-master path connects acquisition, application-managed storage, parsing, lookup, and quality reporting; no general market-data fetch-to-report pipeline connects other data sources to reports or a dashboard.

## 2. High-level component architecture

```text
                         ┌────────────────────────┐
                         │ Configuration / Settings│
                         └───────────┬────────────┘
                                     │
                                     ▼
┌──────────────────┐    ┌─────────────────────────┐
│ Data providers   │───▶│ Processing and validation│
└──────────────────┘    └────────────┬────────────┘
                                     ▼
                           ┌──────────────────┐
                           │ Analytics        │
                           └───────┬──────────┘
                                   ▼
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
          ┌──────────────────┐          ┌──────────────────┐
          │ Streamlit dashboard│          │ Report generation│
          └──────────────────┘          └──────────────────┘
```

**Implemented:** instrument-master acquisition, storage, lookup, and quality reporting support the provider/processing boundary. **Proposed:** manual and scheduled runs should invoke the same broader application workflow to avoid divergent behavior.

## 3. Component responsibilities

### Configuration

**Implemented:** `Settings` is a frozen dataclass providing application name/version, `MARKETPULSE_ENV`, Kotak consumer-key/environment configuration, the project root, and derived data paths. No settings loader creates directories or validates live credentials.

### Data providers

**Implemented:** `KotakNeoProvider` wraps four client methods: `get_scrip_master`, `search_instruments`, `get_quotes`, and `get_historical_data`. It accepts an injected client for tests; otherwise it checks for a consumer key and constructs `NeoAPI` with consumer key and environment. It forwards SDK results without imposing a response schema.

**Proposed:** other sources (for example news, corporate announcements, global markets, commodities, and currency data) should be encapsulated in separate adapters selected according to availability, licensing, and freshness needs.

### Instrument master service

**Implemented:** `InstrumentMasterAcquirer` obtains a segment-specific HTTPS location through the provider, uses the bounded certifi-TLS downloader, decodes UTF-8 CSV, and passes it to the store. `InstrumentMasterStore` saves and loads CSV text by exchange segment under `settings.instrument_dir`, using `<segment>.csv` filenames. It validates segment names against path traversal and basic CSV structure, preserves input text and headers, and uses atomic replacement. It does not fetch data itself or validate freshness.

**Implemented:** `InstrumentMasterParser` maps the required and recognized optional Kotak columns into the source-neutral `Instrument` model and rejects malformed structure or missing required headers/values. `InstrumentMasterService` loads and parses instruments and supports exact symbol lookups. Its `get_quality_report(exchange_segment)` loads the CSV once, parses it with the existing parser, extracts original `pInstType` values with the standard-library CSV reader, and passes both aligned inputs to `InstrumentMasterQualityReporter`. A missing `pInstType` column produces a clear `ValueError`; ordinary instrument loading remains independent of that source column.

**Implemented:** `InstrumentMasterQualityReporter` is a standalone, non-rejecting reporter. It reports total records; exchange- and market-segment distributions; required and optional field completeness; duplicate tokens, symbols, trading symbols, and ISINs; lot-size distributions and numeric zero/negative observations using `Decimal`; series distribution; and blank counts/distribution for source `pInstType` values. Blank identifiers are excluded from duplicate counts. It preserves unusual lot-size strings and does not infer undocumented Kotak field meanings.

**Implemented:** the local NSE Cash snapshot is kept under Git-ignored `data/instruments/nse_cm.csv`. Real-data regression and service integration tests use it when available, skip when absent, and avoid pinning its record count or distributions. At the 6M documentation update, the complete unittest suite has 94 passing tests.

### Market data processing

**Proposed:** processing should perform source-aware validation, timestamp handling, normalization, and missing-data reporting. It should preserve provenance and avoid assuming common field names across providers. Pandas and CSV are candidate tools/formats where they fit the data shape.

### Analytics

**Proposed:** analytics should calculate report inputs such as index/stock moves, sector summaries, and market breadth from validated inputs, and clearly indicate when source coverage is incomplete. FII/DII, news, corporate announcements, global markets, commodities, and currencies may require separate providers and definitions.

### Dashboard

**Proposed:** a Streamlit application should provide a concise overview, detailed views, timestamps/freshness, and links to generated reports. It should call application services rather than query provider SDKs directly. No dashboard screens exist yet.

### Reports

**Proposed:** report generation should compose a dated report model from validated data and analytics, render a human-readable document, and optionally export it as PDF. Manual and scheduled invocation should share this same path. No templates or PDF generation code exist yet.

## 4. Kotak Neo integration

- **Confirmed:** project metadata pins `kotakneoapi==3.0.13`; the installed environment reports SDK version 3.0.13.
- **Implemented:** the current wrapper is in `app/marketpulse/data/kotak_neo.py`. Its four exposed operations are scrip master, instrument search, quotes, and historical data. It passes through return values and does not define normalized result schemas.
- **Implemented:** if it constructs the SDK client, it requires `KOTAK_CONSUMER_KEY` and passes that value plus `KOTAK_ENVIRONMENT` to `NeoAPI`. Tests can inject a client and avoid SDK construction.
- **Confirmed:** SDK inspection shows the scrip-master operation makes a request using the consumer key in an Authorization header and returns metadata or a segment-specific file path depending on the call. The SDK's `search_scrip` path downloads CSV content for a requested segment, reads it with Pandas, and caches the downloaded bytes locally by segment and calendar day. Its default cache directory is `~/.kotak_neo/scrip_cache`; `NEO_SCRIP_CACHE_DIR` can override it. Stale files for that segment are removed when a new day's cache is written.
- **Implemented:** the application wrapper contains no TOTP/MPIN login flow and no trade-token validation. It exposes no trading or order-related methods.
- **Open Question:** live consumer-key-only access has not yet been verified by this project. No live API call or SDK client initialization has been performed for this documentation.
- **Open Question:** confirm access requirements for quotes and historical data in the intended Kotak account/environment before relying on them. The wrapper itself does not establish a live authenticated session.

The SDK may include order-related APIs, but the application design must not expose or invoke them.

## 5. Instrument master design

- **Confirmed:** the SDK's `scrip_master` method retrieves scrip-master metadata and can return a file path for a requested exchange segment. The SDK's search implementation obtains segment CSV bytes, uses Pandas to parse them, and caches its own copy locally for the calendar day. This SDK-managed cache is separate from application storage.
- **Implemented:** application-managed CSV text is stored under the configured `data/instruments/` location, with separate `<segment>.csv` files. Storage validates CSV structure and atomically replaces the matching segment file only after validation.
- **Implemented:** the store preserves the provided CSV text and header names without field normalization. It does not create storage directories until a save is requested.
- **Implemented:** segment-specific files can be acquired over HTTPS and saved through the application store. The current flow does not attach retrieval timestamps or validate freshness.
- **Proposed:** add a separate mapping/normalization layer only when verified fields and downstream needs are known.
- **Open Question:** determine refresh cadence, retention/history policy, cache invalidation behavior, and whether the application should reuse SDK-discovered files or download/own its own copies.
- **Confirmed:** the locally retained NSE Cash snapshot contains the columns consumed by the current Kotak parser, including `pSymbol`, `pExchSeg`, `pSymbolName`, and `pTrdSymbol`. The parser maps only its known columns; remaining provider fields are not assigned undocumented semantics.

## 6. Market data processing architecture

**Proposed:** a pipeline should have explicit stages: acquire provider response, attach retrieval context, validate source result, parse into a source-aware representation, normalize only verified common concepts, compute analytics inputs, and report completeness/freshness. Failures should be visible to downstream reports rather than silently interpreted as zero or unchanged values.

**Implemented:** a general multi-source ingestion and analytics pipeline does not yet exist. Instrument-master acquisition and quality reporting are implemented as a focused path; the provider wrapper continues to forward SDK calls without defining normalized market-data schemas.

## 7. Proposed dashboard architecture

**Proposed:** Streamlit pages or sections should consume application services/use cases and display the latest available report inputs, source timestamps, and availability state. Keep page code focused on presentation. Avoid making broker requests independently from page rendering; a service layer should control fetching and error handling. No Streamlit dependency or dashboard implementation is present in the current project configuration.

## 8. Proposed morning and evening report generation flow

**Proposed morning flow:** trigger a manual run or future scheduled run; gather available pre-market inputs; validate and timestamp them; calculate summaries; render a morning report model; produce a PDF; save the result and run metadata for later display.

**Proposed evening flow:** trigger a manual or scheduled run after session close; gather available end-of-day inputs; validate and timestamp them; calculate market and breadth summaries; render an evening report model; produce a PDF; save the result and run metadata.

Both flows should tolerate optional-source failures and mark missing sections clearly. No report models, PDF files, persistence, or report workflows are implemented.

## 9. Proposed scheduling approach

**Proposed:** start with a local scheduler or operating-system scheduler invoking the same command/use case used by manual runs. Make runs idempotent for a given report date/session, define market-calendar and timezone behavior, and persist run outcomes. Select a scheduler only after operational needs are known.

**Open Question:** choose the scheduling mechanism, holiday-calendar source, retry policy, and behavior when the machine is offline at a scheduled time. No scheduler is currently configured.

## 10. Error handling and logging principles

**Proposed:** distinguish configuration errors, provider/network failures, invalid or stale data, processing failures, and output failures. Include source, operation, and run context in logs while redacting credentials and sensitive values. Reports should show data freshness and partial availability. Use bounded retries only for suitable transient failures and avoid retries that violate provider limits.

**Implemented:** the wrapper raises `KotakConfigurationError` when a consumer key is absent before constructing the SDK client. It otherwise forwards SDK behavior without application-level error mapping or logging policy.

## 11. Security considerations

- **Implemented:** the consumer key is read from `KOTAK_CONSUMER_KEY`; it is not hard-coded in the provider module.
- **Proposed:** keep credentials out of source control, logs, generated reports, and exception text. Limit access to any local secret configuration and data containing account-specific information.
- **Proposed:** preserve the read-only boundary through interfaces, tests, and code review. Do not add order placement, modification, cancellation, or automated trading flows.
- **Confirmed:** this project has not verified live consumer-key-only access. The existence of an SDK method does not by itself establish account authorization or production availability.
- **Proposed:** validate third-party inputs and avoid logging raw payloads if they may contain sensitive information.

## 12. Open technical questions

- **Open Question:** Which authoritative sources and licenses will cover news, corporate announcements, FII/DII data, global markets, commodities, and currency data?
- **Open Question:** What data freshness and retention guarantees are required for reports and dashboard history?
- **Open Question:** What exact Kotak segment files, fields, and date semantics are available to the intended account?
- **Open Question:** Which quote and historical-data calls can be made before or without a completed 2FA session in the target setup?
- **Open Question:** Should generated reports and snapshots remain local CSV/files, or is a database needed for the initial product?
- **Open Question:** Which PDF rendering approach and report template format best fit the intended output?
- **Open Question:** Which local scheduler and market-calendar source should govern report timings, holidays, and missed runs?
- **Open Question:** What Streamlit page structure and report-history UX are needed for the first usable release?
