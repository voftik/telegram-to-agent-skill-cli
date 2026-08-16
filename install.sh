#!/bin/bash
# Idempotent installer: CLI + agent skill + auto-activation snippets.
# Run from a clone of the repository. Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.agents/skills"
MARKER="<!-- tg-skill -->"

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

# Data dir resolution MUST match src/tg_cli/config.py (#31):
# DATA_DIR > XDG_DATA_HOME/tg-cli > platform default.
resolve_data_dir() {
    if [ -n "${DATA_DIR:-}" ]; then
        printf '%s\n' "$DATA_DIR"
    elif [ -n "${XDG_DATA_HOME:-}" ]; then
        printf '%s\n' "$XDG_DATA_HOME/tg-cli"
    elif [ "$(uname -s)" = "Darwin" ]; then
        printf '%s\n' "$HOME/Library/Application Support/tg-cli"
    else
        printf '%s\n' "$HOME/.local/share/tg-cli"
    fi
}
DATA_DIR_RESOLVED="$(resolve_data_dir)"

# Replace LINK with a symlink to TARGET; refuse to clobber real files/dirs.
link_safely() { # TARGET LINK
    local target="$1" link="$2"
    if [ -L "$link" ] || [ ! -e "$link" ]; then
        ln -sfn "$target" "$link"
    else
        fail "$link exists and is not a symlink — move it away and re-run"
    fi
    local resolved
    resolved="$(readlink "$link")"
    [ "$resolved" = "$target" ] || fail "symlink check failed: $link -> $resolved (expected $target)"
}

step "1/5 CLI (uv tool, editable from this clone)"
command -v uv >/dev/null 2>&1 || fail "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
uv tool install --editable "$REPO_DIR" 2>&1 | tail -1 \
    || uv tool install --reinstall --editable "$REPO_DIR" 2>&1 | tail -1
# Absolute entrypoint — works even when uv's tool bin is not in PATH yet (#31).
TG_BIN="$(uv tool dir)/telegram-to-agent-skill-cli/bin/tg"
[ -x "$TG_BIN" ] || fail "tg entrypoint not found at $TG_BIN"
"$TG_BIN" --version
if ! command -v tg >/dev/null 2>&1; then
    printf '\033[33mNote: uv tool bin is not in PATH; run `uv tool update-shell` and reopen the terminal.\033[0m\n'
fi

step "2/5 Data dir and credentials ($DATA_DIR_RESOLVED)"
mkdir -p "$DATA_DIR_RESOLVED" && chmod 700 "$DATA_DIR_RESOLVED"
if [ ! -f "$DATA_DIR_RESOLVED/.env" ]; then
    cat > "$DATA_DIR_RESOLVED/.env" <<'ENV'
# Get your own at https://my.telegram.org -> API development tools
TG_API_ID=
TG_API_HASH=
ENV
    echo "Created $DATA_DIR_RESOLVED/.env — fill TG_API_ID / TG_API_HASH before first run."
else
    echo ".env already present — keeping it."
fi
chmod 600 "$DATA_DIR_RESOLVED/.env"

step "3/5 Agent skill symlinks"
mkdir -p "$SKILLS_DIR" "$HOME/.claude/skills"
link_safely "$REPO_DIR/skill" "$SKILLS_DIR/tg"
link_safely "$SKILLS_DIR/tg" "$HOME/.claude/skills/tg"
echo "skill -> $SKILLS_DIR/tg -> ~/.claude/skills/tg"

step "4/5 Auto-activation snippets"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if ! grep -qF "$MARKER" "$CLAUDE_MD" 2>/dev/null; then
    cat >> "$CLAUDE_MD" <<SNIPPET

$MARKER
## Telegram-контекст (скилл tg)
Когда пользователь упоминает Telegram-чаты, каналы или переписку («что обсуждали в чатике X», «коллеги скинули», «найди в телеграме», «саммари канала», «ответь в чат») — используй скилл tg (~/.claude/skills/tg) и CLI \`tg\`. Read-only по умолчанию; запись в Telegram (send/edit/delete) только с \`--confirm\` после явного «да» пользователя в текущей сессии. Не применяй скилл к задачам разработки Telegram-ботов (Bot API).
SNIPPET
    echo "snippet appended to ~/.claude/CLAUDE.md"
else
    echo "~/.claude/CLAUDE.md already has the snippet."
fi

if [ -d "$HOME/.codex" ]; then
    AGENTS_MD="$HOME/.codex/AGENTS.md"
    if ! grep -qF "$MARKER" "$AGENTS_MD" 2>/dev/null; then
        cat >> "$AGENTS_MD" <<SNIPPET

$MARKER
## Telegram context (tg CLI)
The user's Telegram account is synced locally by the \`tg\` CLI (installed via uv). When the user mentions Telegram chats, channels or correspondence ("что обсуждали в чатике", "коллеги скинули", "найди в телеграме", "саммари канала", "ответь в чат") — use it. Read the full playbook first: run \`cat ~/.agents/skills/tg/SKILL.md\` (scenarios live in the references/ subfolder next to it).
Hard rules: read-only by default; every Telegram write (send/edit/delete) needs the user's explicit "yes" in the current session and the \`--confirm\` flag (without it all three are dry-runs). Default reading depth: 7 days or 200 messages — run \`tg brief CHAT\` before going deeper. All commands support --yaml. Not for Telegram Bot API development tasks.
SNIPPET
        echo "snippet appended to ~/.codex/AGENTS.md"
    else
        echo "~/.codex/AGENTS.md already has the snippet."
    fi
fi

step "5/5 Self-check (offline-safe, never prompts)"
"$TG_BIN" status --yaml | head -6 || true
cat <<'NEXT'

Next steps if this is a fresh machine:
  1. Fill TG_API_ID / TG_API_HASH in the .env above
  2. tg whoami             # phone -> code from Telegram -> 2FA
  3. tg bootstrap start    # initial sync: survives reboots, removes itself when done
NEXT
