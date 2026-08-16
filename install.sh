#!/bin/bash
# Idempotent installer: CLI + agent skill + auto-activation snippets.
# Run from a clone of the repository. Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${DATA_DIR:-$HOME/Library/Application Support/tg-cli}"
SKILLS_DIR="$HOME/.agents/skills"
MARKER="<!-- tg-skill -->"

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

step "1/5 CLI (uv tool, editable from this clone)"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
uv tool install --editable "$REPO_DIR" 2>&1 | tail -1 || uv tool install --reinstall --editable "$REPO_DIR" 2>&1 | tail -1
tg --version

step "2/5 Data dir and credentials"
mkdir -p "$DATA_DIR" && chmod 700 "$DATA_DIR"
if [ ! -f "$DATA_DIR/.env" ]; then
  cat > "$DATA_DIR/.env" <<'ENV'
# Get your own at https://my.telegram.org -> API development tools
TG_API_ID=
TG_API_HASH=
ENV
  chmod 600 "$DATA_DIR/.env"
  echo "Created $DATA_DIR/.env — fill TG_API_ID / TG_API_HASH before first run."
else
  echo ".env already present — keeping it."
fi

step "3/5 Agent skill symlinks"
mkdir -p "$SKILLS_DIR" "$HOME/.claude/skills"
ln -sfn "$REPO_DIR/skill" "$SKILLS_DIR/tg"
ln -sfn "$SKILLS_DIR/tg" "$HOME/.claude/skills/tg"
echo "skill -> $SKILLS_DIR/tg -> ~/.claude/skills/tg"

step "4/5 Auto-activation snippets"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if ! grep -qF "$MARKER" "$CLAUDE_MD" 2>/dev/null; then
  cat >> "$CLAUDE_MD" <<SNIPPET

$MARKER
## Telegram-контекст (скилл tg)
Когда пользователь упоминает Telegram-чаты, каналы или переписку («что обсуждали в чатике X», «коллеги скинули», «найди в телеграме», «саммари канала», «ответь в чат») — используй скилл tg (~/.claude/skills/tg) и CLI \`tg\`. Read-only по умолчанию; отправка сообщений только через \`tg send --confirm\` после явного «да» пользователя в текущей сессии. Не применяй скилл к задачам разработки Telegram-ботов (Bot API).
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
Hard rules: read-only by default; sending requires showing the text to the user, an explicit "yes" in the current session, and \`tg send CHAT "text" --confirm\` (without --confirm it is a dry-run). Default reading depth: 7 days or 200 messages — run \`tg brief CHAT\` before going deeper. All commands support --yaml. Not for Telegram Bot API development tasks.
SNIPPET
    echo "snippet appended to ~/.codex/AGENTS.md"
  else
    echo "~/.codex/AGENTS.md already has the snippet."
  fi
fi

step "5/5 Self-check"
tg status --yaml 2>/dev/null | head -4 || echo "Session not authorized yet — run: tg whoami"
cat <<'NEXT'

Next steps if this is a fresh machine:
  1. Fill TG_API_ID / TG_API_HASH in the .env above
  2. tg whoami             # phone -> code from Telegram -> 2FA
  3. tg bootstrap start    # initial sync: survives reboots, removes itself when done
NEXT
