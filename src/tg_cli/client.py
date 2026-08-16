"""Telegram client with connection reuse and entity caching."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat, User

from .config import (
    get_api_hash,
    get_api_id,
    get_session_path,
    is_default_api_id,
)
from .console import console
from .db import MessageDB
from .extract import extract_message_meta

log = logging.getLogger(__name__)

# Telegram Desktop 5.x fingerprint — makes the session look like a real client
_DEVICE_MODEL = "Desktop"
_SYSTEM_VERSION = "macOS 15.3"
_APP_VERSION = "5.12.1"
_LANG_CODE = "en"
_SYSTEM_LANG_CODE = "en-US"

# Progressive sync: limit for first-time chat sync (no prior messages in DB)
_FIRST_SYNC_LIMIT = 500


def _get_sender_name(sender: User | Channel | Chat | None) -> str | None:
    if sender is None:
        return None
    if isinstance(sender, User):
        parts = [sender.first_name or "", sender.last_name or ""]
        name = " ".join(p for p in parts if p)
        return name or sender.username or str(sender.id)
    return getattr(sender, "title", None) or str(sender.id)


_default_api_warned = False


@asynccontextmanager
async def connect() -> AsyncGenerator[TelegramClient, None]:
    """Async context manager for Telegram client — single connection, reuse within scope."""
    global _default_api_warned
    api_id = get_api_id()
    api_hash = get_api_hash()

    if not _default_api_warned and is_default_api_id():
        _default_api_warned = True
        console.print(
            "[yellow]⚠ Using default Telegram Desktop API credentials (api_id=2040).\n"
            "  This increases the risk of account restrictions.\n"
            "  Get your own at https://my.telegram.org and set TG_API_ID / TG_API_HASH.[/yellow]"
        )

    c = TelegramClient(
        get_session_path(),
        api_id,
        api_hash,
        device_model=_DEVICE_MODEL,
        system_version=_SYSTEM_VERSION,
        app_version=_APP_VERSION,
        lang_code=_LANG_CODE,
        system_lang_code=_SYSTEM_LANG_CODE,
    )
    await c.start()
    await _cache_me(c)
    try:
        yield c
    finally:
        await c.disconnect()


async def _cache_me(client: TelegramClient) -> None:
    """Best-effort cache of the account identity so offline commands
    (e.g. `tg style`) know the user's sender_id without connecting."""
    try:
        import json

        from .config import get_data_dir

        me = await client.get_me()
        if me is None:
            return
        payload = {
            "id": me.id,
            "username": me.username,
            "name": _get_sender_name(me),
        }
        (get_data_dir() / "me.json").write_text(json.dumps(payload, ensure_ascii=False))
    except Exception as e:  # pragma: no cover - never break a real command
        log.debug("me.json cache failed: %s", e)


def load_cached_me() -> dict | None:
    """Read the cached account identity, or None when never connected."""
    import json

    from .config import get_data_dir

    path = get_data_dir() / "me.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


async def list_chats(
    client: TelegramClient,
    chat_type: str | None = None,
) -> list[dict]:
    """List all dialogs (chats/groups/channels) the user has joined."""
    results = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        t = "unknown"
        if isinstance(entity, User):
            t = "user"
        elif isinstance(entity, Chat):
            t = "group"
        elif isinstance(entity, Channel):
            t = "channel" if entity.broadcast else "supergroup"

        if chat_type and t != chat_type:
            continue

        results.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "type": t,
                "unread": dialog.unread_count,
            }
        )
    return results


async def get_chat_info(client: TelegramClient, chat: str | int) -> dict | None:
    """Get detailed information about a chat."""
    try:
        entity = await client.get_entity(chat)
    except Exception as e:
        log.debug("get_chat_info failed for %s: %s", chat, e)
        return None

    info: dict[str, str] = {}
    info["Title"] = getattr(entity, "title", None) or getattr(entity, "first_name", "") or str(chat)
    info["ID"] = str(entity.id)

    if isinstance(entity, User):
        info["Type"] = "User"
        info["Username"] = f"@{entity.username}" if entity.username else "—"
        info["Phone"] = entity.phone or "—"
    elif isinstance(entity, Chat):
        info["Type"] = "Group"
        info["Members"] = str(getattr(entity, "participants_count", "?"))
    elif isinstance(entity, Channel):
        info["Type"] = "Channel" if entity.broadcast else "Supergroup"
        info["Username"] = f"@{entity.username}" if entity.username else "—"
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest

            full = await client(GetFullChannelRequest(entity))
            info["Members"] = str(full.full_chat.participants_count or "?")
            if full.full_chat.about:
                info["Description"] = full.full_chat.about[:200]
        except Exception as e:
            info["Members"] = "?"
            log.debug("Failed to get full channel info: %s", e)

    return info


class _Ingest:
    """Shared message-processing pipeline for history fetches and gap fills.

    Batches are committed as iteration goes, so everything processed before
    an interruption is already durable — the caller only needs to record the
    unfetched remainder as a gap cursor.
    """

    BATCH_SIZE = 200

    def __init__(self, db: MessageDB, chat_id: int, chat_name: str):
        self.db = db
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.sender_cache: dict[int, str] = {}
        self.batch: list[dict] = []
        self.att_batch: list[dict] = []
        self.link_batch: list[dict] = []
        self.stored = 0
        self.seen = 0
        self.oldest_seen: int | None = None

    def add(self, msg) -> bool:
        """Process one message; returns True when a batch was flushed."""
        self.seen += 1
        self.oldest_seen = msg.id  # newest-first iteration: last seen = oldest

        meta = extract_message_meta(msg)
        # Media-only messages (a file without caption) must be kept;
        # service messages (no text, no media) are skipped.
        if msg.text is None and msg.message is None and not meta["has_media"]:
            return False

        sender_name = None
        if msg.sender_id:
            if msg.sender_id in self.sender_cache:
                sender_name = self.sender_cache[msg.sender_id]
            else:
                # Telethon caches sender in msg._sender from the response
                cached = getattr(msg, "_sender", None) or getattr(msg, "sender", None)
                if cached:
                    sender_name = _get_sender_name(cached)
                if sender_name:
                    self.sender_cache[msg.sender_id] = sender_name

        content = msg.text or msg.message or ""
        ts = msg.date
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        self.batch.append(
            dict(
                chat_id=self.chat_id,
                chat_name=self.chat_name,
                msg_id=msg.id,
                sender_id=msg.sender_id,
                sender_name=sender_name,
                content=content,
                timestamp=ts or datetime.now(timezone.utc),
                reply_to_msg_id=meta["reply_to_msg_id"],
                has_media=meta["has_media"],
            )
        )
        if meta["attachment"]:
            self.att_batch.append(dict(chat_id=self.chat_id, msg_id=msg.id, **meta["attachment"]))
        for link in meta["links"]:
            self.link_batch.append(dict(chat_id=self.chat_id, msg_id=msg.id, **link))

        if len(self.batch) >= self.BATCH_SIZE:
            self.flush()
            return True
        return False

    def flush(self) -> None:
        if self.batch:
            self.stored += self.db.insert_batch(self.batch)
            self.batch.clear()
        self.db.insert_attachments(self.att_batch)
        self.db.insert_links(self.link_batch)
        self.att_batch.clear()
        self.link_batch.clear()


async def _ingest_range(
    client: TelegramClient,
    entity,
    ingest: _Ingest,
    *,
    limit: int,
    min_id: int = 0,
    max_id: int = 0,
    on_progress: Callable[[int], None] | None = None,
    batch_delay: float = 0,
) -> str | None:
    """Run one newest-first pass over a message range. Returns an error string
    (FloodWait included) or None; everything fetched so far is committed."""
    try:
        async for msg in client.iter_messages(entity, limit=limit, min_id=min_id, max_id=max_id):
            flushed = ingest.add(msg)
            if flushed:
                if on_progress:
                    on_progress(ingest.stored)
                # Anti-ban: throttle between pagination batches
                if batch_delay > 0:
                    jitter = batch_delay * random.uniform(-0.3, 0.3)
                    await asyncio.sleep(batch_delay + jitter)
        return None
    except FloodWaitError as e:
        console.print(f"[yellow]⚠ Telegram rate limit hit, waiting {e.seconds}s...[/yellow]")
        await asyncio.sleep(e.seconds + random.uniform(1, 3))
        return f"flood_wait:{e.seconds}"
    except Exception as e:
        log.warning("history fetch interrupted for %s: %s", ingest.chat_name, e)
        return str(e)
    finally:
        ingest.flush()


async def fetch_history(
    client: TelegramClient,
    chat: str | int,
    limit: int = 1000,
    db: MessageDB | None = None,
    on_progress: Callable[[int], None] | None = None,
    min_id: int = 0,
    batch_delay: float = 0,
) -> dict:
    """Fetch history newest-first and store it, gap-safely.

    When the pass cannot cover the whole (min_id, newest] range — the limit
    was hit, or an error/FloodWait interrupted iteration — the unfetched
    remainder is recorded in sync_gaps so a later pass can heal it. A capped
    *first* sync (min_id=0) records a 'backfill' cursor instead: older
    history is reachable via `tg backfill`, but is not an integrity hole.

    Returns {"stored", "seen", "status": complete|partial|failed, "error"}.
    """
    owns_db = db is None
    if db is None:
        db = MessageDB()

    try:
        try:
            entity = await client.get_entity(chat)
        except Exception as e:
            return {"stored": 0, "seen": 0, "status": "failed", "error": str(e)}
        chat_name = (
            getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat)
        )
        chat_id = entity.id

        ingest = _Ingest(db, chat_id, chat_name)
        error = await _ingest_range(
            client,
            entity,
            ingest,
            limit=limit,
            min_id=min_id,
            on_progress=on_progress,
            batch_delay=batch_delay,
        )

        status = "complete"
        if error is not None:
            status = "failed" if not error.startswith("flood_wait") else "partial"
        gap_kind = "backfill" if min_id == 0 else "gap"
        if ingest.oldest_seen is not None and (error is not None or ingest.seen >= limit):
            # Remainder (min_id, oldest_seen) was not fetched this pass.
            if db.record_gap(chat_id, min_id, ingest.oldest_seen, kind=gap_kind):
                if error is None and gap_kind == "gap":
                    status = "partial"

        return {"stored": ingest.stored, "seen": ingest.seen, "status": status, "error": error}
    finally:
        if owns_db:
            db.close()


async def fill_gaps(
    client: TelegramClient,
    db: MessageDB,
    chat: str | int,
    *,
    kind: str = "gap",
    limit: int = 2000,
    batch_delay: float = 0,
) -> dict:
    """Heal recorded gap/backfill cursors for one chat.

    Each cursor is consumed newest-first inside its (from_id, to_id) window;
    a partial fill shrinks the cursor so no progress is ever lost.

    Returns {"stored", "closed", "remaining", "error"}.
    """
    try:
        entity = await client.get_entity(chat)
    except Exception as e:
        return {"stored": 0, "closed": 0, "remaining": -1, "error": str(e)}
    chat_id = entity.id
    chat_name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat)

    stored = 0
    closed = 0
    error = None
    for gap in db.get_gaps(chat_id=chat_id, kind=kind):
        ingest = _Ingest(db, chat_id, chat_name)
        error = await _ingest_range(
            client,
            entity,
            ingest,
            limit=limit,
            min_id=gap["from_id"],
            max_id=gap["to_id"],
            batch_delay=batch_delay,
        )
        stored += ingest.stored
        if error is None and ingest.seen < limit:
            db.delete_gap(gap["id"])  # window exhausted — gap closed
            closed += 1
        elif ingest.oldest_seen is not None:
            db.shrink_gap(gap["id"], ingest.oldest_seen)
        if error is not None:
            break
    return {
        "stored": stored,
        "closed": closed,
        "remaining": db.count_gaps(kind=kind, chat_id=chat_id),
        "error": error,
    }


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_attachment_filename(
    msg_id: int,
    raw_name: str | None,
    kind: str,
    mime_type: str | None,
) -> str:
    """Build a filesystem-safe name for a server-supplied attachment name.

    Telegram file names are attacker-controlled: they may contain path
    separators, `..`, absolute paths, control characters or Windows
    reserved names. The result is always a plain basename.
    """
    import mimetypes
    import re

    name = (raw_name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip().lstrip(".")
    stem = name.split(".")[0].upper()
    if not name or stem in _WINDOWS_RESERVED:
        ext = mimetypes.guess_extension(mime_type or "") or ""
        name = f"{kind}_{msg_id}{ext}"
    return f"{msg_id}_{name}"[:200]


async def download_attachments(
    client: TelegramClient,
    db: MessageDB,
    chat: str | int,
    *,
    msg_ids: list[int] | None = None,
    kinds: list[str] | None = None,
    hours: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Download pending attachments for a chat and extract text where possible.

    Files land in <data_dir>/files/<chat_id>/, named <msg_id>_<file_name>.
    Extracted text goes next to the file as <name>.txt. Already-downloaded
    attachments (local_path set and the file still exists) are skipped, so
    re-running never duplicates work.
    """
    import hashlib
    from pathlib import Path

    from .config import get_data_dir
    from .textextract import extract_text, extractable

    entity = await client.get_entity(chat)
    chat_id = entity.id

    rows = db.get_attachments(chat_id=chat_id, hours=hours, limit=max(limit * 5, limit))
    targets = []
    for row in rows:
        if msg_ids and row["msg_id"] not in msg_ids:
            continue
        if kinds and row["kind"] not in kinds:
            continue
        if row["local_path"] and Path(row["local_path"]).exists():
            continue
        targets.append(row)
        if len(targets) >= limit:
            break
    if not targets:
        return []

    files_dir = get_data_dir() / "files" / str(chat_id)
    files_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    by_id = {t["msg_id"]: t for t in targets}
    ids = list(by_id)
    for chunk_start in range(0, len(ids), 100):
        chunk = ids[chunk_start : chunk_start + 100]
        messages = await client.get_messages(entity, ids=chunk)
        for msg in messages or []:
            if msg is None or getattr(msg, "media", None) is None:
                continue
            row = by_id.get(msg.id)
            if row is None:
                continue
            name = safe_attachment_filename(
                msg.id, row["file_name"], row["kind"], row["mime_type"]
            )
            target = files_dir / name
            # Belt and braces: never write outside this chat's directory,
            # never silently overwrite an existing file.
            if not target.resolve().is_relative_to(files_dir.resolve()):
                log.warning("attachment path escapes files dir, skipping: %r", name)
                continue
            stem, dot, ext = name.partition(".")
            counter = 1
            while target.exists():
                target = files_dir / f"{stem}~{counter}{dot}{ext}"
                counter += 1
            try:
                saved = await client.download_media(msg, file=str(target))
            except FloodWaitError as e:
                console.print(f"[yellow]⚠ rate limit, waiting {e.seconds}s...[/yellow]")
                await asyncio.sleep(e.seconds + random.uniform(1, 3))
                continue
            if not saved:
                continue
            saved_path = Path(saved)
            sha = hashlib.sha256(saved_path.read_bytes()).hexdigest()
            text_path = None
            if extractable(saved_path):
                text = extract_text(saved_path)
                if text:
                    tp = saved_path.with_name(saved_path.name + ".txt")
                    tp.write_text(text, encoding="utf-8")
                    text_path = str(tp)
            db.mark_attachment_downloaded(
                chat_id,
                msg.id,
                local_path=str(saved_path),
                sha256=sha,
                text_path=text_path,
                file_name=saved_path.name,
            )
            results.append(
                {
                    "msg_id": msg.id,
                    "kind": row["kind"],
                    "local_path": str(saved_path),
                    "text_path": text_path,
                    "sha256": sha,
                }
            )
    return results


async def sync_all(
    client: TelegramClient,
    db: MessageDB,
    limit_per_chat: int = 5000,
    on_chat_done: Callable[[str, int, int], None] | None = None,
    delay: float = 1.0,
    max_chats: int | None = None,
) -> dict[str, int]:
    """Sync all chats in the database using a single connection.

    Args:
        on_chat_done: Callback(chat_name, new_count, total_in_chat)
        delay: Seconds to wait between each chat sync (with ±20% jitter).
            Set to 0 to disable. Helps avoid triggering Telegram rate limits.
        max_chats: Max number of chats to sync per run. None = no limit.

    Returns a pass report; "results" is keyed by chat_id:
        {
          "enumerated": bool,     # dialog listing itself succeeded
          "error": str | None,    # enumeration error when not enumerated
          "total": int, "ok": int, "partial": int, "failed": int,
          "new_messages": int,
          "results": {chat_id: {"name", "new", "status", "error"}},
        }
    A pass proves nothing when "enumerated" is False or "failed" > 0 —
    callers (bootstrap) must not treat such a pass as a completed sync.
    """
    report: dict = {
        "enumerated": False,
        "error": None,
        "total": 0,
        "ok": 0,
        "partial": 0,
        "failed": 0,
        "new_messages": 0,
        "results": {},
    }
    stored_chats = {c["chat_id"]: c for c in db.get_chats()}
    dialog_cache: dict[int, tuple[object, str]] = {}
    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            dialog_cache[entity.id] = (entity, dialog.name)
    except Exception as e:
        # A failed enumeration must never look like an empty-but-successful
        # pass (#19) — report it explicitly.
        report["error"] = f"dialog enumeration failed: {e}"
        return report
    report["enumerated"] = True

    items = list(dialog_cache.items())
    if max_chats is not None:
        items = items[:max_chats]
    report["total"] = len(items)

    for idx, (chat_id, (entity, dialog_name)) in enumerate(items):
        chat_info = stored_chats.get(chat_id, {})
        chat_name = chat_info.get("chat_name") or dialog_name or str(chat_id)
        last_id = db.get_last_msg_id(chat_id) or 0

        # Progressive sync: use lower limit for first-time chat sync
        effective_limit = limit_per_chat
        if last_id == 0 and limit_per_chat > _FIRST_SYNC_LIMIT:
            effective_limit = _FIRST_SYNC_LIMIT
            log.debug("First sync for %s, limiting to %d messages", chat_name, effective_limit)

        new_count = 0
        try:
            # Heal integrity gaps left by earlier capped/interrupted passes
            # before advancing the checkpoint further (#22).
            if db.count_gaps(kind="gap", chat_id=chat_id):
                gap_res = await fill_gaps(client, db, entity, limit=effective_limit)
                new_count += gap_res["stored"]
                if gap_res["error"]:
                    raise RuntimeError(gap_res["error"])

            res = await fetch_history(
                client,
                entity,
                limit=effective_limit,
                db=db,
                min_id=last_id,
            )
            new_count += res["stored"]
            status = res["status"]
            error = res["error"]
        except Exception as e:
            status = "failed"
            error = str(e)
            console.print(f"  [red]✗ {chat_name}: {e}[/red]")

        report["results"][chat_id] = {
            "name": chat_name,
            "new": new_count,
            "status": status,
            "error": error,
        }
        report["new_messages"] += new_count
        key = status if status in ("ok", "partial", "failed") else "ok"
        if status == "complete":
            key = "ok"
        report[key] += 1
        if on_chat_done and status != "failed":
            on_chat_done(chat_name, new_count, chat_info.get("msg_count", 0) + new_count)

        # Anti-ban: sleep with random jitter between chat syncs
        if delay > 0 and idx < report["total"] - 1:
            jitter = delay * random.uniform(-0.2, 0.2)
            await asyncio.sleep(delay + jitter)

    return report


async def listen(
    client: TelegramClient,
    chats: list[str | int] | None = None,
    db: MessageDB | None = None,
):
    """Real-time listen for new messages in specified chats (or all chats)."""
    owns_db = db is None
    if db is None:
        db = MessageDB()

    try:
        me = await client.get_me()
        console.print(f"[green]✓[/green] Logged in as [bold]{me.first_name}[/bold] ({me.phone})")
        console.print("[dim]Listening for messages... Press Ctrl+C to stop.[/dim]")

        @client.on(events.NewMessage(chats=chats))
        async def handler(event):
            msg = event.message
            chat = await event.get_chat()
            sender = await event.get_sender()

            chat_name = (
                getattr(chat, "title", None) or getattr(chat, "first_name", None) or "Unknown"
            )
            sender_name = _get_sender_name(sender)
            content = msg.text or msg.message or ""

            ts = msg.date
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            meta = extract_message_meta(msg)
            db.insert_message(
                chat_id=chat.id,
                chat_name=chat_name,
                msg_id=msg.id,
                sender_id=msg.sender_id,
                sender_name=sender_name,
                content=content,
                timestamp=ts or datetime.now(timezone.utc),
                reply_to_msg_id=meta["reply_to_msg_id"],
                has_media=meta["has_media"],
            )
            if meta["attachment"]:
                db.insert_attachments([dict(chat_id=chat.id, msg_id=msg.id, **meta["attachment"])])
            if meta["links"]:
                db.insert_links(
                    [dict(chat_id=chat.id, msg_id=msg.id, **link) for link in meta["links"]]
                )

            time_str = ts.strftime("%H:%M:%S") if ts else "??:??:??"
            console.print(
                f"[dim]{time_str}[/dim] [cyan]{chat_name}[/cyan] | "
                f"[bold]{sender_name or 'Unknown'}[/bold]: {content[:200]}"
            )

        status = "disconnected"
        try:
            await client.run_until_disconnected()
        except KeyboardInterrupt:
            status = "stopped"
            console.print("\n[yellow]Stopped listening.[/yellow]")
        finally:
            db_count = db.count()
            console.print(f"[green]Total messages in DB: {db_count}[/green]")
        return status
    finally:
        if owns_db:
            db.close()
