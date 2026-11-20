#!/bin/bash
# Remove iMessage Explorer from this Mac.
#
#   bash uninstall.sh                      removes the app and its code; keeps your data
#   bash uninstall.sh --delete-my-data     also removes your local messages database, labels and topics
#
# Your real Messages are never touched either way.

set -euo pipefail

APP_NAME="iMessage Explorer"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"

for apps in "${IMX_INSTALL_APPS_DIR:-}" "/Applications" "$HOME/Applications"; do
  if [ -n "$apps" ] && [ -d "$apps/$APP_NAME.app" ]; then
    rm -rf "$apps/$APP_NAME.app"
    printf 'Removed %s\n' "$apps/$APP_NAME.app"
  fi
done
if [ -d "$SUPPORT" ]; then
  rm -rf "$SUPPORT"
  printf 'Removed %s\n' "$SUPPORT"
fi

if [ "${1:-}" = "--delete-my-data" ]; then
  rm -rf "$HOME/.imessage-explorer"
  printf 'Removed your local database, labels and topics in %s\n' "$HOME/.imessage-explorer"
else
  printf 'Your local database, labels and topics are still in %s\n' "$HOME/.imessage-explorer"
  printf 'To remove those too, run:  bash uninstall.sh --delete-my-data\n'
fi
