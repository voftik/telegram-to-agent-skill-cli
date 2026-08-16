# telegram-to-agent-skill-cli

Telegram as context for coding agents. A fork of
[jackwener/tg-cli](https://github.com/jackwener/tg-cli) that turns your
personal Telegram account into a local, searchable knowledge source for
Claude Code, Codex and any agent that can run a CLI — with an agent skill
that activates itself when you mention your chats.

The CLI logs in as you (MTProto via Telethon), syncs chats into a local
SQLite index and answers from that index. Agents read locally — fast, no
rate limits, no token-hungry MCP tool schemas; only sync, file downloads
and (explicitly confirmed) sending touch Telegram.

## What the fork adds over upstream

| Area | Upstream tg-cli | This fork |
| --- | --- | --- |
| Attachments | not stored | indexed at sync; `tg files --download` fetches lazily and extracts text (pdf, docx, xlsx, pptx, csv…) |
| Media-only messages | silently skipped | kept (file without caption is still a message) |
| Links | not extracted | `tg links` with agent-fetchable `fetch_url` — Google Docs/Sheets/Slides rewritten to export endpoints |
| Threads | — | `tg thread` reconstructs reply chains (incl. unsynced roots) |
| Search | `LIKE` substring scan | SQLite FTS5 (prefixes, phrases) with substring fallback |
| Own voice | — | `tg style` — corpus of your messages for drafting replies as you |
| Send safety | sends immediately | dry-run by default, `--confirm` + `sent.log` audit |
| Agent integration | SKILL.md doc | installable skill with playbooks + auto-activation snippets for Claude Code and Codex |
| Voice messages | — | downloaded; transcription hook in schema (v2) |

## Quick start

```bash
git clone https://github.com/voftik/telegram-to-agent-skill-cli.git
cd telegram-to-agent-skill-cli && ./install.sh
# fill TG_API_ID/TG_API_HASH (my.telegram.org), then:
tg whoami && tg refresh
```

Full onboarding, including credentials and background initial sync:
[docs/INSTALL.md](docs/INSTALL.md). The agent skill with hard rules and
scenario playbooks lives in [skill/](skill/).

## Security model

- The Telethon session file grants **full account access**: it stays in a
  `700` data dir, never in git, never in cloud-synced folders; each machine
  authorizes separately.
- Sending is impossible without `--confirm`; agents are instructed to show
  the text and get an explicit "yes" first. Every send is logged.
- Use your own `api_id`/`api_hash`; automation via user API can be
  throttled or flagged by Telegram — sync politely (`--delay`), read
  locally.

## Credits

Built on [tg-cli](https://github.com/jackwener/tg-cli) by
[@jackwener](https://github.com/jackwener) (Apache-2.0) and
[Telethon](https://github.com/LonamiWebs/Telethon). Base command reference
(sync, search, listen, export…) is documented upstream and unchanged here.
License: Apache-2.0.
