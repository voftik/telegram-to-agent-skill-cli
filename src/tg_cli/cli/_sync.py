"""Shared sync helpers for CLI commands."""

from __future__ import annotations

from collections.abc import Callable

from ..client import connect, fetch_history, fill_gaps, sync_all
from ..db import MessageDB
from ._chat import _parse_chat


async def sync_all_dialogs(
    *,
    limit: int,
    on_chat_done: Callable[[str, int, int], None] | None = None,
    delay: float = 1.0,
    max_chats: int | None = None,
) -> dict:
    """Sync all dialogs; returns the sync_all pass report."""
    with MessageDB() as db:
        async with connect() as client:
            return await sync_all(
                client,
                db,
                limit_per_chat=limit,
                on_chat_done=on_chat_done,
                delay=delay,
                max_chats=max_chats,
            )


async def sync_chat_dialog(
    chat: str,
    *,
    limit: int,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """Sync a single chat: heal recorded gaps first, then fetch new messages.

    Returns the fetch_history report with gap-fill counts folded in.
    """
    with MessageDB() as db:
        chat_id = db.resolve_chat_id(chat)
        last_id = db.get_last_msg_id(chat_id) if chat_id else 0
        async with connect() as client:
            healed = 0
            if chat_id and db.count_gaps(kind="gap", chat_id=chat_id):
                gap_res = await fill_gaps(client, db, _parse_chat(chat), limit=limit)
                healed = gap_res["stored"]
                if gap_res["error"]:
                    return {
                        "stored": healed,
                        "seen": healed,
                        "status": "failed",
                        "error": gap_res["error"],
                    }
            res = await fetch_history(
                client,
                _parse_chat(chat),
                limit=limit,
                db=db,
                on_progress=on_progress,
                min_id=last_id or 0,
            )
            res["stored"] += healed
            return res


async def backfill_chat_dialog(chat: str, *, limit: int) -> dict:
    """Consume 'backfill' cursors — pull history older than the first sync."""
    with MessageDB() as db:
        async with connect() as client:
            return await fill_gaps(
                client, db, _parse_chat(chat), kind="backfill", limit=limit
            )
