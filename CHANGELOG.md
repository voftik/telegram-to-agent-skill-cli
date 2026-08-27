# Changelog

All notable changes to this project will be documented in this file.

## 0.8.0 (2026-08-27) — fork: telegram-to-agent-skill-cli

Built-in credentials by default and desktop chat apps as first-class hosts.

- Setup: Enter (or `--yes` with no flags) continues on the built-in
  Telegram Desktop keys; own keys stay recommended for heavy syncing.
  BREAKING-ish: `tg setup --yes` without `--api-id/--api-hash` now exits 0
- Setup: paired credential validation (a lone TG_API_ID no longer dies
  with ApiIdInvalidError later), retry loops on bad interactive input,
  my.telegram.org pitfall warning (the reserved word "telegram" breaks
  the form with a bare ERROR)
- MCP: `tg mcp`, a read-only stdio MCP server over the local index for
  Claude Desktop, Perplexity and ChatGPT desktop; 6 tools, no Telegram
  session, no sends, zero new dependencies
- Connect: `tg connect` detects the apps, writes their configs (JSON
  merge with .bak for Claude Desktop, bounded TOML section for
  ~/.codex/config.toml shared with ChatGPT desktop), prints the
  Perplexity walkthrough, and self-tests the bridge with a real MCP
  handshake before the user opens the app; new wizard step + `--apps`
- Autosync: `tg autosync start` arms a scheduled background refresh pass
  (launchd / systemd user timer, default 15 min) that survives reboots
  and steps aside while the bootstrap initial sync is pending
- Docs: docs/DESKTOP-APPS.md guide, INSTALL.md rewritten for keyless
  setup, honest MCP section update in both READMEs
- Dist: plugin metadata version now guarded against pyproject drift

## 0.7.0 (2026-08-17) — fork: telegram-to-agent-skill-cli

Fork of jackwener/tg-cli focused on agent integration. Highlights:

- Attachments: indexed at sync, lazy `tg files --download`, text extraction
  (pdf/docx/xlsx/pptx/csv) with zip-bomb and size budgets
- Links: `tg links` with agent-fetchable `fetch_url` (Google Docs/Sheets/
  Slides export endpoints), structural URL classification
- Threads (`tg thread`, t.me link parsing), `tg brief`, `tg style`, FTS5
  search with Unicode-correct fallback and gap-safe regex paging
- Gap-safe sync cursors + `tg backfill`; honest per-chat pass reports
- Marked peer IDs end-to-end with lazy legacy migration
- Reboot-resilient initial sync (`tg bootstrap`, launchd/systemd)
- Safety: send/edit/delete are dry-run by default with `--confirm` and a
  durable mutation journal; private file permissions; hardened installer
- Agent skill (shipped inside the wheel) with auto-activation for Claude
  Code and Codex; Claude Code plugin marketplace in-repo
- `tg setup` interactive wizard, `tg update` self-update with PyPI check,
  `tg skill install/status/uninstall`
- Release pipeline: PyPI (Trusted Publishing), npm launcher, GitHub
  Releases on version tags
- Hardened storage (atomic batches, crash-safe migration, private file
  modes), resource budgets for untrusted attachments, uniform CLI error
  contract, safe rendering of hostile chat text, local-timezone buckets

## 0.4.3 - 2026-03-11

- Use Telegram Desktop built-in API credentials (API_ID=2040) as defaults; users no longer need to apply for their own app credentials
- Updated README and SKILL.md to reflect zero-config authentication

## 0.4.1 - 2026-03-10

- Fixed GitHub publish workflow permissions so PyPI checkout can read repository contents
- Fixed ClawHub publish workflow to use the Node.js payload workaround for `acceptLicenseTerms`

## 0.4.0 - 2026-03-10

- Switched the project license to Apache-2.0
- Removed built-in Telegram app credentials; users now provide `TG_API_ID` and `TG_API_HASH`
- Added YAML output support and documented YAML as the preferred agent format
- Added `tg recent`
- Added regex search with `tg search --regex`
- Added `tg refresh` as the recommended daily refresh entrypoint
- Added `--sync-first` to query commands
- Added `tg listen --persist` for automatic reconnect
- Improved local query safety with chat ambiguity detection and clearer `today` hints
- Added cron and systemd examples for scheduled refresh
