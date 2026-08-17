# Changelog

All notable changes to this project will be documented in this file.

## 0.7.0-dev (fork: telegram-to-agent-skill-cli)

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
- Agent skill (`skill/`) with auto-activation for Claude Code and Codex

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
