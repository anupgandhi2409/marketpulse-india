# MarketPulse India – Technical Framework Guide

## 1. Project overview and purpose

MarketPulse India is a planned daily market-intelligence and reporting application focused on Indian markets, especially NSE and BSE. Its intended purpose is to assemble relevant market information into useful morning and evening views, with PDF reports and a Streamlit dashboard.

**Implemented foundation:** the repository contains a Python package, immutable settings, a read-only Kotak Neo provider wrapper, and unit tests. The reporting, dashboard, ingestion, and analytics capabilities described below are planned and are not yet implemented.

## 2. Project objectives

- Provide a consistent view of Indian market conditions before and after the trading session.
- Collect market information through clearly separated provider adapters.
- Turn validated source data into understandable summaries and analysis.
- Make reports available both on demand and, when scheduled workflows are added, automatically.
- Keep broker integration read-only and keep credentials outside source control.
- Preserve a modular foundation that can be developed and tested locally.

## 3. Functional scope

The intended product scope includes:

- NSE and BSE market intelligence.
- Morning pre-market reports and evening post-market reports.
- PDF report generation, initiated manually or through a future schedule.
- A Streamlit dashboard.
- Coverage for market indices, stocks, sectors, market breadth, FII/DII activity, news, corporate announcements, global markets, commodities, and currencies.

These are product requirements, not claims that the corresponding pipelines or screens exist today. Data availability and licensing will need to be established for each source and category.

## 4. Explicit exclusions

- No automated trading.
- No order placement, modification, or cancellation.
- No WhatsApp notifications in the initial scope.
- No cloud deployment requirement in the initial scope; local development is the starting point.

## 5. Technology stack

| Area | Current or intended choice | Status |
| --- | --- | --- |
| Runtime | Python 3.13 (`>=3.13,<3.14`) | Configured |
| Dashboard | Streamlit | Planned |
| Broker SDK | Kotak Neo SDK 3.0.13 (`kotakneoapi==3.0.13`) | Dependency configured; wrapper implemented |
| Data handling | Pandas and CSV where appropriate | Intended; no application pipeline yet |
| Tests | `unittest` currently; `pytest` configured as a development dependency | Test suite implemented; pytest is optional dev dependency |

The project metadata currently declares the Kotak SDK as its runtime dependency and `pytest` in the `dev` optional dependency group. The tests presently use Python's `unittest` framework. Pandas and Streamlit are not declared project dependencies in the current configuration.

## 6. Current project structure

The following reflects the repository today; package directories other than the concrete modules shown are foundations or placeholders.

```text
marketpulse-india/
├── app/
│   └── marketpulse/
│       ├── __init__.py
│       ├── analytics/
│       │   └── __init__.py
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py
│       ├── dashboard/
│       │   └── __init__.py
│       ├── data/
│       │   ├── __init__.py
│       │   └── kotak_neo.py
│       └── reports/
│           └── __init__.py
├── tests/
│   ├── __init__.py
│   ├── test_kotak_neo.py
│   └── test_settings.py
├── docs/
│   ├── framework.md
│   └── technical-design.md
└── pyproject.toml
```

The `analytics` and `dashboard` packages currently contain only package initializers; `reports` likewise has no report implementation. No instrument data or application-managed data directories are currently present.

## 7. Application architecture and module responsibilities

**Implemented:**

- `marketpulse.config.settings`: frozen `Settings` dataclass, environment-backed configuration, project root, and derived `data_dir` and `instrument_dir` paths.
- `marketpulse.data.kotak_neo`: a thin Kotak Neo SDK wrapper exposing scrip master, instrument search, quotes, and historical data methods. It forwards calls and leaves response interpretation to future layers.
- `tests`: `unittest` coverage for settings and wrapper behavior, using mocks rather than live broker calls.

**Planned:**

- Provider adapters for external market information.
- Instrument-master storage and lookup services.
- Data normalization and market-data processing.
- Analytics for indices, securities, sectors, breadth, flows, and other report sections.
- A Streamlit presentation layer.
- Report composition, PDF output, and scheduling.

These responsibilities should remain separated so that provider-specific behavior does not leak into analytics or presentation code.

## 8. Configuration and environment variables

The `Settings` dataclass in `app/marketpulse/config/settings.py` reads the following variables when a settings instance is created:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKETPULSE_ENV` | `development` | Application environment label |
| `KOTAK_CONSUMER_KEY` | unset (`None`) | Kotak Neo consumer key; required by the provider when it constructs an SDK client |
| `KOTAK_ENVIRONMENT` | `prod` | Kotak SDK environment value |

Settings are frozen after construction. `project_root` is derived from the settings module path. `data_dir` and `instrument_dir` are computed as `<project_root>/data` and `<project_root>/data/instruments`; these properties do not create directories.

## 9. Data storage principles

No application storage layer is implemented yet. The current settings only define locations for future data. Planned local CSV storage should use explicit exchange-segment separation, retain source and retrieval-time context where appropriate, and avoid silently overwriting a valid snapshot with an invalid or partial download. Retention, history, and any database choice remain future design decisions. Do not assume the Kotak SDK's own cache is the application's managed instrument store.

## 10. Security and broker integration principles

- Keep the provider boundary read-only. The application scope excludes all order and trading operations.
- Keep consumer keys and future credentials in environment configuration or an appropriately protected secret store; do not commit them.
- Avoid logging secrets or sensitive account information.
- Keep SDK calls behind the provider wrapper so tests can inject a mock client.
- The wrapper's source does not implement TOTP/MPIN login or trade-token validation. Live access behavior has not been verified by this project.

## 11. Testing strategy

The current tests use `unittest` and mock the SDK constructor/client. They verify settings defaults and overrides, frozen settings, derived paths, missing-key handling, and argument forwarding for provider methods. They do not contact Kotak services. `pytest` is configured as a development dependency, but the present suite is written with `unittest`.

As features are added, tests should cover transformations, validation, report assembly, and failure paths with fixed sample inputs. Live broker calls should not be required for routine unit tests.

## 12. Local development workflow

1. Use the repository's Python 3.13 virtual environment.
2. Install the project and development extras only as needed for development.
3. Configure environment variables locally; keep real credentials out of tracked files.
4. Run the unit suite with `.venv/bin/python -m unittest discover -s tests -v`.
5. Keep new work within the existing package layout and add tests around behavior that can be tested without external services.

There is not yet an application entry point or runnable dashboard.

## 13. Development principles and architectural decisions

- Clearly distinguish implemented behavior from proposed features.
- Keep integrations, processing, analytics, reports, and presentation loosely coupled.
- Prefer small provider wrappers and dependency injection for testability.
- Treat external data as untrusted until validated; do not invent undocumented schemas.
- Start with local operation and avoid adding deployment infrastructure without a product requirement.
- Maintain a strict read-only broker boundary.
- Add storage and scheduling only with defined freshness, retry, and recovery behavior.

## 14. Glossary

| Term | Meaning |
| --- | --- |
| NSE / BSE | National Stock Exchange of India / BSE Ltd. |
| Instrument master (scrip master) | Provider-supplied reference listing used to identify tradable or quoted instruments; exact fields depend on the provider file. |
| Exchange segment | A provider-defined market segment such as an equity or derivatives segment. Use SDK-supported values rather than assuming a universal naming scheme. |
| Market breadth | Summary of advancing, declining, and unchanged securities, subject to data coverage and a defined universe. |
| FII / DII | Foreign Institutional Investor / Domestic Institutional Investor. |
| Provider adapter | A module that isolates one external data source behind application-facing methods. |
| Pre-market report | A summary prepared before the regular trading session. |
| Post-market report | A review prepared after the regular trading session. |
