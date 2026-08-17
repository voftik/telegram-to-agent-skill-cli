---
name: tg
description: >-
  Use the user's Telegram chats as context: search messages, read chat
  history, summarize discussions, reconstruct threads, download and read
  attached files (pdf/docx/xlsx/pptx, images), extract shared links
  (Google Docs → fetchable export URLs), draft replies in the user's own
  style. Trigger whenever the user mentions Telegram chats, channels or
  correspondence: "посмотри в чатике", "что обсуждали", "коллеги
  скинули", "найди в телеграме", "саммари канала", "что там в чате",
  "ответь в телеграм", "telegram chat", "напомни ход обсуждения".
  Do NOT use for Telegram Bot API / bot development tasks.
---

# tg — Telegram as agent context

CLI `tg` logs into the user's personal Telegram account (MTProto), syncs
chats into a local SQLite index and answers from that index. Reading is
local and cheap; only sync and file download touch Telegram.

## Hard rules

1. **Read-only by default.** Every write to Telegram — `send`, `edit`,
   `delete` — is a two-step ritual: show the exact text/operation to the
   user, get an explicit "yes" **in this session**, only then re-run with
   `--confirm`. Without `--confirm` all three are dry-runs — use that for
   previews. Confirmed mutations are journaled to `mutations.log`. Never
   invent a reason to send, edit or delete.
2. **Default depth: 7 days or 200 messages**, whichever is smaller. If the
   task seems to need more, run `tg brief CHAT` first, show the numbers and
   ask the user how deep to go.
3. **Never pull more than 200 messages into context without a brief.**
   Prefer `--yaml` everywhere — it is compact and parseable.
4. **Freshness:** the index is as fresh as the last sync. Before analyzing
   a specific chat run `tg sync "CHAT"` (incremental, fast). A background
   initial sync may still be running on a new install — data can be partial.
5. **Files:** `tg files CHAT --download` stores files and extracts text
   into `text_path` (pdf, docx, xlsx, pptx, csv…). Read `text_path` for
   documents; read images directly with your vision (Read tool).
   Voice messages download but are not transcribed (v2).
6. **Links:** `tg links CHAT` returns `url` and `fetch_url`. Always fetch
   `fetch_url`, not `url` — for Google Docs/Sheets/Slides it is the export
   endpoint (plain text/CSV); the original URL returns a JS shell.
   `kind=tme` links are Telegram messages — resolve via
   `tg thread --url <link>`, not via web fetch.
7. **One session per account.** If a command that connects (sync, files
   --download, send) fails with a database-lock / session error, another
   process is using the session (e.g. initial sync). Tell the user and
   retry later; never retry in a loop.
8. **Privacy:** chat content is personal data. Quote only what the task
   needs; do not copy large excerpts into artifacts or commit them to git.

## Command reference (all support --yaml)

| Task | Command |
| --- | --- |
| Resolve chat name | `tg chats --yaml` and match by name (search only finds message *content*) |
| Chat passport (size, activity, files, links) | `tg brief CHAT` |
| Incremental sync of one chat | `tg sync "CHAT"` |
| Recent messages | `tg recent -c CHAT --hours 48 -n 200` |
| Full-text search (FTS5) | `tg search "запрос*" -c CHAT --hours 168` |
| Regex search | `tg search "pattern" --regex` |
| Reply thread around a message | `tg thread CHAT --msg-id N` / `--url t.me/c/…` |
| List attachments | `tg files CHAT --type document -n 50` |
| Download files + extract text | `tg files CHAT --download --type document` |
| Shared links with fetchable URLs | `tg links CHAT --hours 168 --kind gdoc` |
| My own messages (style corpus) | `tg style -n 300` |
| Activity stats | `tg top -c CHAT --hours 168`, `tg timeline -c CHAT` |
| Send (after explicit user "yes") | `tg send CHAT "text" --confirm` |

Search tips: FTS5 has no Russian morphology — use prefixes (`договор*`
matches договорились/договорённость) and try 2–3 word forms; substring
fallback engages automatically when FTS misses.

## Scenarios

- **Analyze a chat** (summary, timeline, decisions, open items):
  [references/analyze-chat.md](references/analyze-chat.md)
- **Digest of several chats/channels**:
  [references/digest.md](references/digest.md)
- **Draft a reply as the user**:
  [references/reply-as-me.md](references/reply-as-me.md)
