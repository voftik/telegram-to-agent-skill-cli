# Digest of several chats / channels

Goal: sweep a list of chats or public channels over a time window and
produce one readable digest — for catching up, planning, or an Obsidian
note.

## Workflow

1. **Fix the list and the window.** If the user named chats — use them;
   "мои рабочие чаты" without a list → propose candidates from `tg chats`
   (`tg stats --yaml` shows *all-time* volumes; for recent activity check
   `tg brief CHAT` or `tg top -c CHAT --hours 48`) and confirm. Default window:
   24–48h for chats, 7 days for channels; confirm if unclear.
2. **Sync each:** `tg sync "CHAT"` in sequence (never in parallel — one
   Telegram session).
3. **Collect per chat:** `tg recent -c CHAT --hours <H> -n 200 --yaml`.
   Cap: if a chat has more than ~200 messages in the window, don't pull
   everything: `tg top -c CHAT --hours <H>` shows *who* is most active
   (senders, no msg_id), then locate the hot discussions via
   `tg search "тема*" -c CHAT` and unroll them with `tg thread --msg-id`.
4. **Enrich:** `tg links CHAT --hours <H>` — a shared doc is often the real
   payload; fetch gdoc/gsheet `fetch_url` when a link is central to the
   discussion.
5. **Compose the digest:**
   - group by chat, order by importance to the user (mentions of the user,
     direct questions to them, decisions > general chatter)
   - per chat: 2–5 bullets with dates/authors, then "❗ требует реакции"
     items if any
   - end with a cross-chat block: действия на сегодня, приоритеты, дедлайны
6. **Deliver** where asked: chat reply, or an Obsidian note (follow the
   user's vault conventions), or a schedule/plan.

Keep the digest shorter than the source by an order of magnitude. Every
"требует реакции" item must cite the chat and msg_id.
