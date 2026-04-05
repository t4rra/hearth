# Hearth

Hearth is a macOS desktop app for syncing books from an OPDS catalog to a Kindle.

The primary install path is now the installer script.

## Recommended Install (macOS)

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

## What The Installer Does

The installer is intended to be end-to-end for GUI users.

- Checks for Homebrew and stops with instructions if Homebrew is missing.
- Installs required Homebrew dependencies:
  - `go`
  - `libusb`
  - `pkg-config`
  - `calibre` (cask)
- Clones (or updates) KCC to `~/.hearth/vendor/kcc`.
- Copies MTP bridge sources to `~/.hearth/vendor/mtpx_bridge` for reliable runtime builds.
- Creates a private Python virtual environment at `~/.hearth/app/venv`.
- Installs Hearth and KCC Python package dependencies into that environment.
- Creates a Finder-launchable app bundle at `~/Applications/Hearth.app`.
- Cleans source-tree build artifacts not needed after install (`build/`, `dist/`, `hearth.egg-info`).
- Configures launcher environment so Homebrew tools are discoverable from Finder launches.

## Uninstall (Full Removal)

Run the uninstaller to remove Hearth app + Hearth data directory (`~/.hearth`):

```bash
./scripts/uninstall_Hearth.sh
```

In interactive mode, the uninstaller will ask whether you want to keep
your local settings file (`~/.hearth/settings.json`).

Non-interactive mode:

```bash
./scripts/uninstall_Hearth.sh --yes
```

`--yes` skips prompts and performs full removal of `~/.hearth`.

Optional: also remove Homebrew dependencies that the installer uses:

```bash
./scripts/uninstall_Hearth.sh --remove-brew-deps
```

With both options:

```bash
./scripts/uninstall_Hearth.sh --yes --remove-brew-deps
```

## Notes

- The app bundle created by this script is not signed/notarized.
- Depending on macOS Gatekeeper behavior, first launch may still require
  an allow/open confirmation.
- The private virtual environment is only used when launching Hearth;
  it does not run in the background by itself.

## GUI Flow

- On startup, Hearth probes for a connected Kindle and displays status.
- On startup, Hearth attempts to reach OPDS and lazy-loads the first collection layer.
- Configure OPDS auth and transport in the `Settings` tab.
- Use the `Library` tab to browse/select books and sync.
- Use the `Kindle Files` tab to browse, download, and delete files on device.

## CLI (Optional)

Hearth includes a CLI (`hearth`) for automation/headless workflows.
The GUI does not shell out to the CLI; both call shared sync logic directly.

Dry-run discovery:

```bash
~/.hearth/app/venv/bin/hearth --feed-url "https://your-opds-server.example/opds" --dry-run
```

## Development / Tests

If you are developing Hearth itself:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install pytest
pytest
```
