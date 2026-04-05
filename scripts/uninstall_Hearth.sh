#!/usr/bin/env bash
set -euo pipefail

HEARTH_HOME="${HEARTH_HOME:-$HOME/.hearth}"
INSTALL_ROOT="${HEARTH_INSTALL_ROOT:-$HEARTH_HOME/app}"
APP_DIR="$HOME/Applications/Hearth.app"
ASSUME_YES="false"
REMOVE_HOMEBREW_DEPS="false"

for arg in "$@"; do
  if [[ "$arg" == "--yes" ]]; then
    ASSUME_YES="true"
  elif [[ "$arg" == "--remove-brew-deps" ]]; then
    REMOVE_HOMEBREW_DEPS="true"
  else
    echo "error: unknown option: $arg" >&2
    echo "usage: $0 [--yes] [--remove-brew-deps]" >&2
    exit 2
  fi
done

if [[ "$ASSUME_YES" != "true" ]]; then
  echo "This will remove:"
  echo "  - $APP_DIR"
  echo "  - $HEARTH_HOME"
  if [[ "$REMOVE_HOMEBREW_DEPS" == "true" ]]; then
    echo "  - Homebrew packages: calibre (cask), go, libusb, pkg-config"
  fi
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
fi

if [[ -d "$APP_DIR" ]]; then
  rm -rf "$APP_DIR"
  echo "Removed $APP_DIR"
else
  echo "No app bundle found at $APP_DIR"
fi

if [[ -d "$HEARTH_HOME" ]]; then
  rm -rf "$HEARTH_HOME"
  echo "Removed $HEARTH_HOME"
else
  echo "No Hearth data directory found at $HEARTH_HOME"
fi

if [[ "$REMOVE_HOMEBREW_DEPS" == "true" ]]; then
  if command -v brew >/dev/null 2>&1; then
    if brew list --cask calibre >/dev/null 2>&1; then
      brew uninstall --cask calibre
    fi
    for formula in go libusb pkg-config; do
      if brew list --formula "$formula" >/dev/null 2>&1; then
        brew uninstall "$formula"
      fi
    done
    echo "Requested Homebrew dependency uninstall complete."
  else
    echo "Homebrew not found; skipped dependency removal."
  fi
fi

echo "Uninstall complete."
