# MarketPulse India

MarketPulse India is a planned daily intelligence and reporting application for the Indian stock markets, with a focus on NSE and BSE. It is intended to collect market information from Kotak Neo Trade API and other configurable providers, prepare pre-market and post-market intelligence, and present reports through PDF documents and a lightweight dashboard.

> **Project status:** Initial workspace setup. The integrations and application features described below are planned; none are implemented yet.

## Planned features

- **Configurable market data collection:** Read market information through Kotak Neo Trade API and, where useful, other provider integrations.
- **Morning pre-market report:** Summarize relevant information before the trading session.
- **Evening post-market report:** Present a review of market activity after the session.
- **PDF reports:** Produce professionally formatted reports for both sessions.
- **Streamlit dashboard:** Provide a lightweight interface for viewing market intelligence and report history.
- **Manual and scheduled runs:** Allow report generation on demand and through a scheduler.
- **Historical records:** Preserve generated reports and market snapshots for later reference.
- **Future delivery options:** WhatsApp delivery is a possible future capability and is not implemented.

All listed features are planned and subject to development. No API integration, report generation, dashboard, scheduling, or delivery functionality is currently available.

## Proposed technology stack

| Area | Technology | Status |
| --- | --- | --- |
| Language | Python | Selected direction |
| Broker market data | Kotak Neo Trade API | Planned; read-only use |
| Other market data | Configurable provider adapters | Planned |
| Data processing | Pandas | Planned |
| Dashboard | Streamlit | Planned |
| Persistence | PostgreSQL | Planned |
| Report templates | Jinja2 | Planned |
| PDF rendering | WeasyPrint | Planned |
| Scheduling | APScheduler | Planned |
| Automated checks | Pytest | Planned |

The stack may be refined as implementation progresses. Dependency files and packages have not yet been added.

## Architecture principles

- Keep the design modular and provider-independent so market data sources can be changed or extended.
- Separate data collection, processing, analysis, reporting, persistence, scheduling, and presentation responsibilities.
- Keep broker integrations strictly read-only. Do not place orders or provide automated trading functionality.
- Leave room for future API-based integration with a separate Trading Assistant application.
- Keep credentials and other secrets out of source control.

## Planned project structure

The following is a proposed structure, not a description of files that already exist:

```text
marketpulse-india/
├── .venv/                     # Local Python environment (ignored by Git)
├── .vscode/                   # Project-specific editor settings
├── data/                      # Local generated data; placeholder may be tracked
├── docs/                      # Project and architecture documentation
├── logs/                      # Local runtime logs
├── reports/                   # Generated PDF reports
├── src/
│   └── marketpulse/
│       ├── providers/          # Read-only, provider-specific data adapters
│       ├── processing/         # Data cleaning and transformation
│       ├── analysis/           # Market intelligence logic
│       ├── reporting/          # Templates and PDF generation
│       ├── storage/            # Persistence interfaces and implementations
│       ├── scheduling/         # Manual and scheduled workflows
│       └── dashboard/          # Streamlit presentation layer
└── tests/                      # Automated tests
```

The structure will be introduced incrementally as the corresponding components are approved and implemented.

## Development roadmap

The roadmap is a plan; completion of workspace setup does not imply that application features are available.

1. **M1 – Project foundation:** Inspect the workspace and establish basic project configuration and documentation.
2. **M2 – Application skeleton:** Add a minimal modular Python package and development configuration.
3. **M3 – Provider interfaces:** Define provider-independent contracts and implement read-only market data access.
4. **M4 – Data processing and analysis:** Normalize snapshots and build initial market intelligence calculations.
5. **M5 – Report generation:** Create pre-market and post-market report templates and PDF output.
6. **M6 – Persistence and history:** Store snapshots and reports, with PostgreSQL as the planned database.
7. **M7 – Dashboard and scheduling:** Add the Streamlit interface and manual/scheduled report workflows.
8. **Later – Integration and delivery:** Explore API integration with Trading Assistant and optional WhatsApp delivery.

Each milestone will be developed incrementally. Later milestones will not be implemented without explicit approval.

## Setup prerequisites

For the current workspace setup:

- Python 3.13.3 is available.
- Git is initialized on the `main` branch.
- A project virtual environment exists at `.venv/`.
- VS Code project settings point to `.venv/bin/python` and enable terminal environment activation.

Activate the environment from the project root:

```bash
source .venv/bin/activate
```

No application dependencies are installed yet, and there is no runnable application entry point. Additional prerequisites, including provider credentials and PostgreSQL, will be documented when those features are implemented.

## Security considerations

- Broker access must remain read-only. Do not add order placement, trade execution, or automated trading features.
- Store API credentials and secrets outside tracked source files. Local `.env` files are ignored by Git; use a sanitized `.env.example` for documenting required variable names if needed.
- Use the least access needed for market data collection and never commit credentials, tokens, or private account data.
- Validate and handle provider data carefully, and avoid exposing sensitive values in logs or reports.
- Any future integration with Trading Assistant should use a documented API boundary and preserve the read-only broker constraint.

## Current project status

**Stage:** Workspace foundation; application implementation has not started.

The repository currently contains the initial Git setup, a project virtual environment, VS Code project settings, and a `.gitignore`. No application code, dependencies, API integrations, reports, database schema, dashboard, or scheduler have been created.
