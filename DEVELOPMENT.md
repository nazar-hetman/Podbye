# Development guide

Podbye is a Windows desktop application built with Python and PySide6. It
analyzes local storage, presents results for review, and keeps cleanup actions
explicit and reversible by default.

## Setup

Use Python 3.12 or a compatible supported Python version on Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the application with:

```powershell
python main.py
```

Run the test suite with:

```powershell
python -m pytest
```

For packaged builds and installer instructions, see [BUILD.md](BUILD.md).

## Project structure

- `app/` — application UI, models, services, and localization resources.
- `tests/` — automated behavior, safety, localization, and layout tests.
- `installer/` — Windows installer source and installer-facing documentation.
- `docs/` — user-facing documentation assets, when needed.

The storage-analysis model is described in
[SEMANTIC_PIPELINE.md](SEMANTIC_PIPELINE.md). UI conventions are documented in
[DESIGN_RULES.md](DESIGN_RULES.md).

## Privacy boundary

The application has no Podbye cloud service and sends no telemetry. AI can be
local, or it can use a runtime hosted on a user-configured private-LAN machine.
Public AI endpoints are not supported. Treat paths and metadata used for AI
analysis as data that will be sent to that user-configured LAN host.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. In
particular, preserve cleanup safety behavior, test affected states, and keep
localizations semantic rather than literal.
