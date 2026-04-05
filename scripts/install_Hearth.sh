#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
HEARTH_HOME="${HEARTH_HOME:-$HOME/.hearth}"
INSTALL_ROOT="${HEARTH_INSTALL_ROOT:-$HEARTH_HOME/app}"
VENV_DIR="$INSTALL_ROOT/venv"
APP_DIR="$HOME/Applications/Hearth.app"
LAUNCHER_DIR="$INSTALL_ROOT/bin"
LAUNCHER_SCRIPT="$LAUNCHER_DIR/launch-hearth-gui"
APP_EXECUTABLE="$APP_DIR/Contents/MacOS/Hearth"
APP_PLIST="$APP_DIR/Contents/Info.plist"
KCC_REPO_DIR="${HEARTH_KCC_REPO_DIR:-$HEARTH_HOME/vendor/kcc}"

require_command() {
  local cmd="$1"
  local help_text="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required command not found: $cmd" >&2
    echo "$help_text" >&2
    exit 1
  fi
}

brew_install_formula() {
  local formula="$1"
  if brew list --formula "$formula" >/dev/null 2>&1; then
    echo "brew formula already installed: $formula"
    return
  fi
  echo "installing brew formula: $formula"
  brew install "$formula"
}

brew_install_cask() {
  local cask="$1"
  if brew list --cask "$cask" >/dev/null 2>&1; then
    echo "brew cask already installed: $cask"
    return
  fi
  echo "installing brew cask: $cask"
  brew install --cask "$cask"
}

if ! command -v brew >/dev/null 2>&1; then
  echo "error: Homebrew is required but was not found." >&2
  echo "Install Homebrew first, then re-run this installer:" >&2
  echo "  https://brew.sh" >&2
  exit 1
fi

require_command "$PYTHON_BIN" "Install Python 3.11+ first, then re-run this installer."
require_command git "Install Git first, then re-run this installer."

brew_install_formula go
brew_install_formula libusb
brew_install_formula pkg-config
brew_install_cask calibre

mkdir -p "$INSTALL_ROOT" "$HOME/Applications" "$HEARTH_HOME/vendor"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
"$VENV_DIR/bin/pip" install "$REPO_ROOT"

if [[ ! -d "$KCC_REPO_DIR/.git" ]]; then
  echo "cloning KCC repository to $KCC_REPO_DIR"
  git clone --depth 1 https://github.com/ciromattia/kcc.git "$KCC_REPO_DIR"
else
  echo "updating existing KCC repository in $KCC_REPO_DIR"
  git -C "$KCC_REPO_DIR" pull --ff-only
fi

"$VENV_DIR/bin/pip" install -e "$KCC_REPO_DIR"

mkdir -p "$LAUNCHER_DIR"
cat >"$LAUNCHER_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$VENV_DIR/bin/hearth-gui" "\$@"
EOF
chmod +x "$LAUNCHER_SCRIPT"

mkdir -p "$APP_DIR/Contents/MacOS"
cat >"$APP_PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>Hearth</string>
  <key>CFBundleIdentifier</key>
  <string>io.hearth.app</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Hearth</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

cat >"$APP_EXECUTABLE" <<EOF
#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x "$LAUNCHER_SCRIPT" ]]; then
  /usr/bin/osascript -e 'display alert "Hearth launcher missing" message "Re-run install_macos_gui.sh to repair the installation." as critical'
  exit 1
fi

exec "$LAUNCHER_SCRIPT" "\$@"
EOF
chmod +x "$APP_EXECUTABLE"

/usr/bin/touch "$APP_DIR"

echo "Installed Hearth GUI launcher"
echo "  app bundle: $APP_DIR"
echo "  install root: $INSTALL_ROOT"
echo "  hearth home: $HEARTH_HOME"
echo "  kcc repo: $KCC_REPO_DIR"
echo
echo "Open it from Applications as: Hearth"
