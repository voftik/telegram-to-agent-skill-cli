# Draft a reply as the user

Goal: propose reply drafts that sound like the user, for messages that
need answering. The user always approves before anything is sent.

## Workflow

1. **Understand what needs answering.** `tg recent -c CHAT --hours 48` or
   `tg thread CHAT --msg-id <id>` for the specific question. Collect the
   facts needed for a substantive answer (search the chat, files, links —
   see analyze-chat.md).
2. **Calibrate the voice.**
   - `tg style -c CHAT -n 100 --yaml` — how the user writes *in this chat*
     (register differs between a work group and a friend DM);
   - `tg style -n 200 --yaml` — general habits: greeting or not, sentence
     length, emoji, ты/вы, typical sign-offs.
   - If the user's global instructions (CLAUDE.md / AGENTS.md) point to a
     personal style guide for Russian text — read and apply it. It
     overrides generic habits.
3. **Draft 2–3 variants:** short/business-like, fuller, and (when fitting)
   an informal one. Each must answer the actual question — no filler, no
   "спасибо за ваш вопрос", no AI-slop pleasantries the user never uses.
4. **Show variants to the user.** Dry-run preview is fine:
   `tg send CHAT "text"` (no --confirm prints and sends nothing).
5. **Only after an explicit "да, отправляй" in this session:**
   `tg send CHAT "text" --confirm`. Quote back which variant was sent.
   If the user edits the text — send their edited version verbatim.

## Voice rules of thumb

- Mirror the corpus, not an idealized register: if the user writes lowercase
  without greetings — do the same.
- Match message length to the chat's norm; Telegram replies are short.
- Terminology the user actually uses beats "correct" synonyms.
- When the answer commits the user to something (deadline, money, promise) —
  flag it explicitly above the draft: "⚠️ это обещание сделать X к пятнице".
