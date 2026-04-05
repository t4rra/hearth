#!/usr/bin/env bash
set -euo pipefail

HEARTH_HOME="${HEARTH_HOME:-$HOME/.hearth}"
INSTALL_ROOT="${HEARTH_INSTALL_ROOT:-$HEARTH_HOME/app}"
APP_DIR="$HOME/Applications/Hearth.app"
SETTINGS_FILE="$HEARTH_HOME/settings.json"
ASSUME_YES="false"
REMOVE_HOMEBREW_DEPS="false"
PRESERVE_SETTINGS="false"

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
  if [[ -f "$SETTINGS_FILE" ]]; then
    read -r -p "Keep local Hearth settings file ($SETTINGS_FILE)? [y/N] " keep_reply
    case "$keep_reply" in
      y|Y|yes|YES)
        PRESERVE_SETTINGS="true"
        ;;
      *)
        PRESERVE_SETTINGS="false"
        ;;
    esac
  fi

  echo "This will remove:"
  echo "  - $APP_DIR"
  if [[ "$PRESERVE_SETTINGS" == "true" ]]; then
    echo "  - $HEARTH_HOME (except settings.json)"
  else
    echo "  - $HEARTH_HOME"
  fi
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
  if [[ "$PRESERVE_SETTINGS" == "true" && -f "$SETTINGS_FILE" ]]; then
    temp_settings="$(mktemp -t hearth-settings.XXXXXX.json)"
    cp "$SETTINGS_FILE" "$temp_settings"
    rm -rf "$HEARTH_HOME"
    mkdir -p "$HEARTH_HOME"
    cp "$temp_settings" "$SETTINGS_FILE"
    rm -f "$temp_settings"
    echo "Removed $HEARTH_HOME and preserved $SETTINGS_FILE"
  else
    rm -rf "$HEARTH_HOME"
    echo "Removed $HEARTH_HOME"
  fi
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
