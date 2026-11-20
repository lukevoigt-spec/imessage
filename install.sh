#!/bin/bash
# Install or update iMessage Explorer on a Mac.
#
# The easy way: download the project, then double-click "Double-click to install.command".
# That file simply runs this one.
#
# Or, for people comfortable with Terminal, paste this one line and press Return:
#
#   curl -fsSL https://raw.githubusercontent.com/lukevoigt-spec/imessage/main/install.sh | bash
#
# What it does:
#   1. Puts the app's code in  ~/Library/Application Support/iMessage Explorer/app
#   2. Creates "iMessage Explorer" in your Applications folder, so you can open it like any app
#   3. Checks that it is allowed to read Messages, and shows you the one setting to switch on if not
#   4. Opens the app
#
# Running it again updates the app. Your messages database, labels, pins and
# topics live in ~/.imessage-explorer and are never touched by installing.
#
# It never asks for a password and installs nothing outside your own user folders,
# apart from the app icon in Applications.

set -euo pipefail

REPOSITORY="lukevoigt-spec/imessage"
APP_NAME="iMessage Explorer"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
CODE="$SUPPORT/app"
PORT=8765

say() { printf '%s\n' "$*"; }
fail() { printf '\nPROBLEM: %s\n' "$*" >&2; exit 1; }

# Tests set IMX_INSTALL_TEST=1: everything is installed into a pretend home folder and nothing is opened.

# ---- 1. checks -----------------------------------------------------------------------------
if [ -z "${IMX_INSTALL_TEST:-}" ] && [ "$(uname)" != "Darwin" ]; then
  fail "iMessage Explorer only runs on a Mac."
fi
if ! command -v python3 >/dev/null 2>&1; then
  fail "Python 3 is not installed. In Terminal run:  xcode-select --install   then run this installer again."
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  fail "Python 3.9 or newer is needed. You have $(python3 --version 2>&1). In Terminal run:  xcode-select --install"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---- 2. get the code -----------------------------------------------------------------------
# If this script sits inside an unpacked copy of the app, install that copy.
# Otherwise (the usual case, when pasted as one line) download the newest published version.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/imx/__init__.py" ] && [ -z "${IMX_INSTALL_FROM_WEB:-}" ]; then
  say "Installing the copy in $SCRIPT_DIR"
  SOURCE="$SCRIPT_DIR"
else
  if [ -n "${IMX_INSTALL_ZIP_URL:-}" ]; then
    ZIP_URL="$IMX_INSTALL_ZIP_URL"                      # used by tests
  else
    say "Downloading the newest version..."
    ZIP_URL="https://github.com/$REPOSITORY/archive/refs/heads/main.zip"
  fi
  curl -fsSL --max-time 120 -o "$WORK/download.zip" "$ZIP_URL" || fail "The download failed. Check the internet connection and try again."
  unzip -q "$WORK/download.zip" -d "$WORK/unpacked" || fail "The download could not be unpacked."
  SOURCE="$(find "$WORK/unpacked" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

for needed in imessage_explorer.py imx/__init__.py imx/data/vader_lexicon.txt imx/static/app.js; do
  [ -f "$SOURCE/$needed" ] || fail "The copy to install is incomplete (missing $needed). Nothing was changed."
done
VERSION="$(python3 -c 'import re, sys; print(re.search(r"__version__\s*=\s*\"([^\"]+)\"", open(sys.argv[1]).read()).group(1))' "$SOURCE/imx/__init__.py")"

# ---- 3. put the code in place ----------------------------------------------------------------
mkdir -p "$SUPPORT"
rm -rf "$SUPPORT/app.new"
mkdir -p "$SUPPORT/app.new"
# Copy only what the app needs to run. Nothing else in the folder is installed.
cp -R "$SOURCE/imx" "$SUPPORT/app.new/imx"
cp "$SOURCE/imessage_explorer.py" "$SUPPORT/app.new/"
for extra in README.md uninstall.sh install.sh; do
  if [ -f "$SOURCE/$extra" ]; then cp "$SOURCE/$extra" "$SUPPORT/app.new/"; fi
done
find "$SUPPORT/app.new" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
printf 'Installed by install.sh. This copy may update itself from inside the app.\n' > "$SUPPORT/app.new/.installed-by-installer"
# A copy downloaded with a web browser carries a "came from the internet" mark. Clear it on the installed copy.
xattr -dr com.apple.quarantine "$SUPPORT/app.new" 2>/dev/null || true
if [ -d "$CODE" ]; then
  rm -rf "$SUPPORT/app.previous"
  mv "$CODE" "$SUPPORT/app.previous"
fi
mv "$SUPPORT/app.new" "$CODE"

# ---- 4. the launcher the app icon runs ---------------------------------------------------------
cat > "$SUPPORT/run.command" <<'RUN'
#!/bin/bash
# Starts iMessage Explorer. Opened by the app icon. Leave this window open while you use the app.
printf '\033]0;iMessage Explorer (leave this window open)\007'
cd "$(dirname "$0")/app" || exit 1
exec python3 imessage_explorer.py serve --port 8765
RUN
chmod +x "$SUPPORT/run.command"

# ---- 5. the app icon -----------------------------------------------------------------------------
if [ -n "${IMX_INSTALL_APPS_DIR:-}" ]; then
  APPS="$IMX_INSTALL_APPS_DIR"
elif [ -w "/Applications" ]; then
  APPS="/Applications"
else
  APPS="$HOME/Applications"
fi
mkdir -p "$APPS"
BUNDLE="$APPS/$APP_NAME.app"
rm -rf "$BUNDLE"

# What the icon does: if iMessage Explorer is already running, show it. Otherwise start it in a
# Terminal window. It runs inside Terminal on purpose: Terminal is the app that has permission
# to read Messages.
#
# The icon is built here, on this Mac, by Apple's own osacompile. That gives a proper native app
# (a bare script wrapped as an app makes Apple Silicon Macs ask for Rosetta), and because it is
# made locally macOS does not treat it as a download.
applescript_text() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
cat > "$WORK/launcher.applescript" <<EOF
set runFile to "$(applescript_text "$SUPPORT/run.command")"
try
	do shell script "curl -s --max-time 2 http://127.0.0.1:$PORT/ | grep -q imx-token"
	open location "http://127.0.0.1:$PORT/"
on error
	do shell script "open -a Terminal " & quoted form of runFile
end try
EOF

plain_icon() {
  # Fallback for a system without osacompile (every Mac has it; the tests do not).
  mkdir -p "$BUNDLE/Contents/MacOS"
  cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.imessage-explorer.launcher</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
</dict>
</plist>
EOF
  cat > "$BUNDLE/Contents/MacOS/launcher" <<EOF
#!/bin/bash
if curl -s --max-time 2 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q 'imx-token'; then
  open "http://127.0.0.1:$PORT/"
else
  open -a Terminal "$SUPPORT/run.command"
fi
EOF
  chmod +x "$BUNDLE/Contents/MacOS/launcher"
}

if command -v osacompile >/dev/null 2>&1; then
  if ! osacompile -o "$BUNDLE" "$WORK/launcher.applescript" >/dev/null 2>&1; then
    rm -rf "$BUNDLE"
    plain_icon
  fi
else
  plain_icon
fi

say ""
say "Installed iMessage Explorer $VERSION."
say "  App:   $BUNDLE"
say "  Code:  $CODE"
say "  Your messages database and labels stay in ~/.imessage-explorer and were not touched."

# ---- 6. permission to read Messages --------------------------------------------------------------
if [ -n "${IMX_INSTALL_TEST:-}" ]; then
  say "[test] would check access to Messages and start the app"
  exit 0
fi
if ! CHECK="$(cd "$CODE" && python3 imessage_explorer.py check 2>&1)"; then
  say ""
  say "One setting is needed before it can read your messages:"
  say "  1. System Settings is opening at Privacy & Security, Full Disk Access."
  say "  2. Switch on Terminal."
  say "  3. Quit Terminal completely, then open iMessage Explorer from Applications."
  say ""
  say "What the check said:"
  printf '%s\n' "$CHECK" | sed 's/^/    /'
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
  exit 0
fi

# ---- 7. start it -------------------------------------------------------------------------------------
say ""
say "From now on, open iMessage Explorer from Applications or Spotlight."
if curl -s --max-time 2 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q 'imx-token'; then
  say "An earlier copy is still running. To switch to the new version: on its Setup screen press"
  say "Quit iMessage Explorer, then open it again from Applications. You can close this window."
  exit 0
fi
say "Starting it now in this window. Leave this window open while you use it."
say ""
exec "$SUPPORT/run.command"
