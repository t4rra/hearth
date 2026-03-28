# Hearth

Hearth is a desktop-oriented Python project for syncing books from an OPDS catalog to a Kindle.

This repository currently includes:

- Core OPDS session/client logic
- Conversion routing abstractions (KCC and Calibre backends)
- Kindle transport and sync state management
- A CLI entrypoint
- A PyQt desktop GUI (`hearth-gui`)
- A pytest test suite (including fixture-driven tests using `TESTING FILES`)

## Prerequisites

- Python 3.11+
- macOS (primary target from current project mandate)

Optional runtime tools (not required for current placeholder conversion logic):

- Kindle Comic Converter CLI
- Calibre (`ebook-convert`)

## Quick Start

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install the project in editable mode:

```bash
pip install -e .
```

3. (Optional) Create a settings file at `.hearth/settings.json`.

Example:

```json
{
  "opds_url": "https://your-opds-server.example/opds",
  "auth_mode": "none",
  "auth_username": "",
  "auth_password": "",
  "auth_bearer_token": "",
  "kindle_transport": "auto",
  "kindle_mount": "",
  "desired_output": "auto",
  "kcc_command": "",
  "calibre_command": ""
}
```

## Run the CLI

### Dry run (discover OPDS items only)

```bash
hearth --feed-url "https://your-opds-server.example/opds" --dry-run
```

### Sync run

```bash
hearth \
  --feed-url "https://your-opds-server.example/opds" \
  --workspace .hearth \
  --kindle-root /path/to/kindle/mount
```

Force re-sync of already tracked items:

```bash
hearth --feed-url "https://your-opds-server.example/opds" --force
```

## Run the GUI

Launch the desktop interface:

```bash
hearth-gui
```

GUI flow:

- Load settings (or edit fields directly)
- Click "Load Library" to pull OPDS acquisitions
- Select rows to sync
- Click "Sync Selected"
- Watch logs/status in the bottom panel

## Run Tests

Install test dependency:

```bash
pip install pytest
```

Run full test suite:

```bash
pytest
```

## Test Fixtures

The `TESTING FILES` directory contains large real-world sample files used by tests to validate format detection and sync behavior paths.

## Current Status

The architecture and sync/test flow are implemented. Converter backends are intentionally structured for integration but currently use placeholder conversion behavior until real subprocess integration is wired in.
