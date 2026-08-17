#!/bin/bash
# Developer install: editable CLI from this clone, then the setup wizard.
# End users don't need this — see docs/INSTALL.md:
#   uv tool install telegram-to-agent-skill-cli && tg setup
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

command -v uv >/dev/null 2>&1 || {
    echo "ERROR: uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
}

uv tool install --reinstall --editable "$REPO_DIR" 2>&1 | tail -1
TG_BIN="$(uv tool dir)/telegram-to-agent-skill-cli/bin/tg"
[ -x "$TG_BIN" ] || { echo "ERROR: tg entrypoint not found at $TG_BIN" >&2; exit 1; }

if ! command -v tg >/dev/null 2>&1; then
    echo "Note: uv tool bin is not in PATH; run \`uv tool update-shell\` and reopen the terminal."
fi

exec "$TG_BIN" setup "$@"
