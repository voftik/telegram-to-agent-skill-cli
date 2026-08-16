# Analyze a chat

Goal: turn a chat's history into what the task needs — a summary, a
timeline, extracted decisions, open commitments, or context for the
current working session.

## Workflow

1. **Resolve the chat.** `tg chats --yaml` and match by name; if several
   match, show candidates and ask. Remember the numeric `chat_id`.
2. **Sync.** `tg sync "CHAT"` — incremental, cheap. Skip only if the user
   says the cache is fresh.
3. **Scope.** `tg brief CHAT --yaml`. Default depth is 7 days / 200
   messages. If the discussion clearly exceeds that (brief shows peaks
   earlier, or the user asked "прочитай целиком"), show the brief numbers
   and confirm the depth before pulling history.
4. **Pull.** `tg recent -c CHAT --hours <H> -n <N> --yaml`. For "who said
   what about X" prefer `tg search "X*" -c CHAT` over pulling everything.
5. **Enrich when the task needs it:**
   - attachments: `tg files CHAT --type document --download`, then read
     `text_path` files; images — Read them directly
   - links: `tg links CHAT --hours <H>` → fetch `fetch_url` for gdoc/gsheet
   - a heated sub-discussion: `tg thread CHAT --msg-id <id>`
6. **Synthesize** in the shape the task asks for.

## Output shapes

- **Summary:** 5–10 bullet points, each anchored with a date and author
  ("14.08, Ксения: решили перенести релиз").
- **Timeline:** chronological list of turning points, not every message.
- **Decision log:** what was decided / by whom / when / status
  (подтверждено, оспорено, отменено позже).
- **Open items:** who promised what, deadline, whether anything happened
  after (search for follow-ups before claiming "не сделано").
- **Working context:** compact digest of constraints/decisions relevant to
  the current coding/writing task, with msg_id references for traceability.

Always state the coverage window explicitly ("по сообщениям с 9 по 16
августа, 143 шт.") so the user knows what the analysis did NOT see.
