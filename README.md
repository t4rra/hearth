# Hearth

Hearth is a macOS desktop app for syncing books from an OPDS catalog to a Kindle.

## Recommended Install (macOS)

> Homebrew is required for the installer to work. 

1. Clone the repository.

```bash
git clone https://github.com/t4rra/hearth.git
cd hearth
```

2. Run the installer.

```bash
./scripts/install_Hearth.sh
```

3. Launch Hearth from Finder at `~/Applications/Hearth.app`.

## Dependencies
- Homebrew (for installing dependencies)

> The following should be installed automatically by the installer
- [Calibre](https://github.com/kovidgoyal/calibre) (for ebook-convert tool used in sync)
- [Kindle Comic Converter (KCC)](https://github.com/ciromattia/kcc)
- [go-mtpx](https://github.com/ganeshrvel/go-mtpx)

## Uninstall (Full Removal)

Run the uninstaller to remove Hearth app + Hearth data directory (`~/.hearth`):

```bash
./scripts/uninstall_Hearth.sh
```

Optional: also remove Homebrew dependencies that the installer uses:

```bash
./scripts/uninstall_Hearth.sh --remove-brew-deps
```

## Notes

- The app bundle created by this script is not signed/notarized.
- Depending on macOS Gatekeeper behavior, first launch may still require
  an allow/open confirmation.

## Setup

1. Select your kindle device
2. Select your connection protocol (MTP or USB)
3. Settings may be imported from your Kindle if you have previously used Hearth, or you can start fresh
4. Enter your OPDS server feed URL and credentials if required
5. It is recommend to visit the settings page and configure the rest before syncing

## CLI (Optional)

Hearth includes a CLI (`hearth`) for automation/headless workflows. This is a leftover from development and is not the primary interface, but it can be used for scripting or debugging. May be modified or removed in the future.
```
➜ hearth --help
usage: hearth [-h] [--settings SETTINGS] [--workspace WORKSPACE] [--feed-url FEED_URL] [--kindle-root KINDLE_ROOT] [--force] [--dry-run]

Sync OPDS books to Kindle

options:
  -h, --help            show this help message and exit
  --settings SETTINGS
  --workspace WORKSPACE
  --feed-url FEED_URL
  --kindle-root KINDLE_ROOT
  --force
  --dry-run
```

## Development / Tests

To run the GUI from the command line, first install the dependencies in a virtual environment, then run `hearth`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
hearth-gui
```
## Disclaimer
I made Hearth with LLMs and have only bothered to test it on my own machine with my own Kindle. It may not work for you, and there may be bugs and security issues. I probably won't be of much help either :) 

