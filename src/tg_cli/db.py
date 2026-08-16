"""SQLite database for storing chat messages."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import get_db_path

log = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT    NOT NULL DEFAULT 'telegram',
    chat_id       INTEGER NOT NULL,
    chat_name     TEXT,
    msg_id        INTEGER NOT NULL,
    sender_id     INTEGER,
    sender_name   TEXT,
    content       TEXT,
    timestamp     TEXT    NOT NULL,
    raw_json      TEXT,
    UNIQUE(platform, chat_id, msg_id)
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_content ON messages(content);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_name);
"""

# Schema v2: attachments, links, threads, FTS5 (fork additions).
# Schema v3: sync_gaps — gap-safe incremental sync cursors (#22).
_SCHEMA_VERSION = 3

_MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS sync_gaps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    from_id    INTEGER NOT NULL,  -- exclusive lower bound (msg_id > from_id)
    to_id      INTEGER NOT NULL,  -- exclusive upper bound (msg_id < to_id)
    kind       TEXT    NOT NULL DEFAULT 'gap',  -- gap: must-fill hole | backfill: older history
    created_at TEXT,
    UNIQUE(chat_id, from_id, to_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_sync_gaps_chat ON sync_gaps(chat_id, kind);
"""

_MIGRATION_V2_TABLES = """
CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    msg_id        INTEGER NOT NULL,
    kind          TEXT    NOT NULL,  -- document|image|voice|video|audio|other
    file_name     TEXT,
    mime_type     TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    local_path    TEXT,
    text_path     TEXT,
    transcript_path TEXT,            -- v2 hook: voice/video transcription
    downloaded_at TEXT,
    UNIQUE(chat_id, msg_id)          -- one media per Telegram message; albums are separate messages
);
CREATE INDEX IF NOT EXISTS idx_attachments_chat ON attachments(chat_id, kind);

CREATE TABLE IF NOT EXISTS links (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    msg_id    INTEGER NOT NULL,
    url       TEXT    NOT NULL,
    fetch_url TEXT,
    kind      TEXT    NOT NULL DEFAULT 'web',  -- gdoc|gsheet|gslides|tme|web
    UNIQUE(chat_id, msg_id, url)
);
CREATE INDEX IF NOT EXISTS idx_links_chat ON links(chat_id, kind);
"""

_MIGRATION_V2_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, coalesce(new.content, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, coalesce(old.content, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, coalesce(old.content, ''));
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, coalesce(new.content, ''));
END;
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to _SCHEMA_VERSION. Idempotent."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= _SCHEMA_VERSION:
        return

    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "reply_to_msg_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to_msg_id INTEGER")
    if "has_media" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN has_media INTEGER NOT NULL DEFAULT 0")

    conn.executescript(_MIGRATION_V2_TABLES)

    fts_existed = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts'"
        ).fetchone()
        is not None
    )
    conn.executescript(_MIGRATION_V2_FTS)
    if not fts_existed:
        # Index everything already stored before the triggers existed.
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")

    conn.executescript(_MIGRATION_V3)

    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    conn.commit()


_CHANNEL_MARK = 1_000_000_000_000


def _chat_id_candidates(chat_id: int) -> list[int]:
    """All marked-ID forms a user-supplied number may refer to (#21).

    The database stores Telethon marked IDs (user 123 / basic group -123 /
    channel -(10^12+123)). A bare positive input is ambiguous, so lookups
    try every marked form; a negative (marked) input also tries its bare
    form so legacy rows keep resolving until their lazy migration runs.
    """
    if chat_id < 0:
        bare = -chat_id
        if bare > _CHANNEL_MARK:
            bare -= _CHANNEL_MARK
        return [chat_id, bare]
    return [chat_id, -chat_id, -(_CHANNEL_MARK + chat_id)]


class MessageDB:
    """SQLite message store with context manager support."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            self.db_path = get_db_path()
        else:
            self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_CREATE_TABLE + _CREATE_INDEX)
        _migrate(self.conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def find_chats(self, chat_str: str) -> list[dict]:
        """Return chats matching a numeric ID, exact name, or partial name."""
        chats = self.get_chats()

        try:
            candidates = _chat_id_candidates(int(chat_str))
            exact_id_matches = [c for c in chats if c["chat_id"] in candidates]
            if exact_id_matches:
                return exact_id_matches
        except ValueError:
            pass

        exact_name_matches = [
            c for c in chats if c["chat_name"] and c["chat_name"].casefold() == chat_str.casefold()
        ]
        if exact_name_matches:
            return exact_name_matches

        partial_matches = [
            c for c in chats if c["chat_name"] and chat_str.casefold() in c["chat_name"].casefold()
        ]
        return partial_matches

    def resolve_chat_id(self, chat_str: str) -> int | None:
        """Resolve a chat string (name or numeric ID) to a unique database chat_id."""
        matches = self.find_chats(chat_str)
        if len(matches) == 1:
            return matches[0]["chat_id"]
        return None

    def insert_message(
        self,
        *,
        platform: str = "telegram",
        chat_id: int,
        chat_name: str | None,
        msg_id: int,
        sender_id: int | None,
        sender_name: str | None,
        content: str | None,
        timestamp: datetime,
        raw_json: dict[str, Any] | None = None,
        reply_to_msg_id: int | None = None,
        has_media: bool = False,
    ) -> bool:
        """Insert a message, returns True if inserted (not duplicate)."""
        try:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO messages
                   (
                       platform,
                       chat_id,
                       chat_name,
                       msg_id,
                       sender_id,
                       sender_name,
                       content,
                       timestamp,
                       raw_json,
                       reply_to_msg_id,
                       has_media
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    platform,
                    chat_id,
                    chat_name,
                    msg_id,
                    sender_id,
                    sender_name,
                    content,
                    timestamp.isoformat(),
                    json.dumps(raw_json, ensure_ascii=False) if raw_json else None,
                    reply_to_msg_id,
                    int(has_media),
                ),
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            log.debug("insert_message failed: %s", e)
            return False

    def insert_batch(self, messages: list[dict], platform: str = "telegram") -> int:
        """Batch insert messages in a single transaction.

        Returns the number of rows actually inserted, excluding duplicates.
        """
        if not messages:
            return 0
        rows = [
            (
                platform,
                m["chat_id"],
                m.get("chat_name"),
                m["msg_id"],
                m.get("sender_id"),
                m.get("sender_name"),
                m.get("content"),
                (
                    m["timestamp"].isoformat()
                    if isinstance(m["timestamp"], datetime)
                    else m["timestamp"]
                ),
                json.dumps(m["raw_json"], ensure_ascii=False) if m.get("raw_json") else None,
                m.get("reply_to_msg_id"),
                int(bool(m.get("has_media"))),
            )
            for m in messages
        ]
        try:
            cursor = self.conn.executemany(
                """INSERT OR IGNORE INTO messages
                   (
                       platform,
                       chat_id,
                       chat_name,
                       msg_id,
                       sender_id,
                       sender_name,
                       content,
                       timestamp,
                       raw_json,
                       reply_to_msg_id,
                       has_media
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.commit()
            # rowcount excludes trigger-driven changes (FTS shadow tables),
            # unlike total_changes, and counts only actually inserted rows.
            return max(cursor.rowcount, 0)
        except sqlite3.Error as e:
            log.warning("insert_batch failed: %s", e)
            return 0

    def insert_attachments(self, rows: list[dict]) -> int:
        """Batch insert attachment metadata. Duplicates are ignored."""
        if not rows:
            return 0
        cursor = self.conn.executemany(
            """INSERT OR IGNORE INTO attachments
               (chat_id, msg_id, kind, file_name, mime_type, size_bytes)
               VALUES (:chat_id, :msg_id, :kind, :file_name, :mime_type, :size_bytes)""",
            rows,
        )
        self.conn.commit()
        return max(cursor.rowcount, 0)

    def insert_links(self, rows: list[dict]) -> int:
        """Batch insert shared links. Duplicates are ignored."""
        if not rows:
            return 0
        cursor = self.conn.executemany(
            """INSERT OR IGNORE INTO links
               (chat_id, msg_id, url, fetch_url, kind)
               VALUES (:chat_id, :msg_id, :url, :fetch_url, :kind)""",
            rows,
        )
        self.conn.commit()
        return max(cursor.rowcount, 0)

    def search(
        self,
        keyword: str,
        chat_id: int | None = None,
        sender: str | None = None,
        hours: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search messages: FTS5 first (prefixes `слово*`, phrases, OR),
        falling back to a LIKE substring scan when FTS finds nothing or
        the query uses characters FTS5 cannot parse."""
        try:
            results = self._search_fts(keyword, chat_id, sender, hours, limit)
        except sqlite3.OperationalError:
            results = []  # fts5 query syntax error — treat as no FTS hits
        if results:
            return results
        return self._search_like(keyword, chat_id, sender, hours, limit)

    def _search_fts(
        self,
        keyword: str,
        chat_id: int | None,
        sender: str | None,
        hours: int | None,
        limit: int,
    ) -> list[dict]:
        query = (
            "SELECT m.* FROM messages_fts f JOIN messages m ON m.id = f.rowid"
            " WHERE messages_fts MATCH ?"
        )
        params: list[Any] = [keyword]
        if chat_id is not None:
            query += " AND m.chat_id = ?"
            params.append(chat_id)
        if sender is not None:
            query += " AND m.sender_name LIKE ?"
            params.append(f"%{sender}%")
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query += " AND m.timestamp >= ?"
            params.append(cutoff)
        query += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def _search_like(
        self,
        keyword: str,
        chat_id: int | None,
        sender: str | None,
        hours: int | None,
        limit: int,
    ) -> list[dict]:
        query = "SELECT * FROM messages WHERE content LIKE ?"
        params: list[Any] = [f"%{keyword}%"]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        if sender is not None:
            query += " AND sender_name LIKE ?"
            params.append(f"%{sender}%")
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query += " AND timestamp >= ?"
            params.append(cutoff)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def search_regex(
        self,
        pattern: str,
        chat_id: int | None = None,
        sender: str | None = None,
        hours: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search messages by regex pattern."""
        regex = re.compile(pattern, re.IGNORECASE)
        query = "SELECT * FROM messages WHERE content IS NOT NULL"
        params: list[Any] = []
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        if sender is not None:
            query += " AND sender_name LIKE ?"
            params.append(f"%{sender}%")
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query += " AND timestamp >= ?"
            params.append(cutoff)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit * 10)

        rows = self.conn.execute(query, params).fetchall()
        results: list[dict] = []
        for row in rows:
            msg = dict(row)
            content = msg.get("content") or ""
            if regex.search(content):
                results.append(msg)
                if len(results) >= limit:
                    break
        return results

    def get_recent(
        self,
        chat_id: int | None = None,
        sender: str | None = None,
        hours: int | None = 24,
        limit: int = 500,
    ) -> list[dict]:
        """Get the latest messages, returned in chronological order."""
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            base_query = "SELECT * FROM messages WHERE timestamp >= ?"
            params: list[Any] = [cutoff]
        else:
            base_query = "SELECT * FROM messages WHERE 1=1"
            params = []
        if chat_id is not None:
            base_query += " AND chat_id = ?"
            params.append(chat_id)
        if sender is not None:
            base_query += " AND sender_name LIKE ?"
            params.append(f"%{sender}%")
        query = (
            f"SELECT * FROM ({base_query} ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC"
        )
        rows = self.conn.execute(query, params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def get_today(
        self,
        chat_id: int | None = None,
        tz_offset_hours: int | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Get today's messages (in local timezone).

        Args:
            tz_offset_hours: Local timezone offset from UTC.
                             If None, auto-detect from system timezone.
        """
        # Today 00:00 in local time → UTC
        now_utc = datetime.now(timezone.utc)
        if tz_offset_hours is not None:
            local_tz = timezone(timedelta(hours=tz_offset_hours))
        else:
            # Auto-detect system timezone
            local_tz = datetime.now().astimezone().tzinfo
        today_local = now_utc.astimezone(local_tz).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        cutoff_utc = today_local.astimezone(timezone.utc).isoformat()

        query = "SELECT * FROM messages WHERE timestamp >= ?"
        params: list[Any] = [cutoff_utc]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY chat_name, timestamp ASC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_chats(self) -> list[dict]:
        """Get all known chats with message counts."""
        rows = self.conn.execute(
            """SELECT chat_id, chat_name, COUNT(*) as msg_count,
                      MIN(timestamp) as first_msg, MAX(timestamp) as last_msg
               FROM messages
               GROUP BY chat_id
               ORDER BY msg_count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_msg_id(self, chat_id: int) -> int | None:
        """Get the latest msg_id for a chat, used for incremental sync."""
        row = self.conn.execute(
            "SELECT MAX(msg_id) FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def count(self, chat_id: int | None = None) -> int:
        if chat_id is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0]

    def get_latest_timestamp(self, chat_id: int | None = None) -> str | None:
        """Return the latest stored message timestamp for a chat or the whole DB."""
        if chat_id is not None:
            row = self.conn.execute(
                "SELECT MAX(timestamp) FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(timestamp) FROM messages").fetchone()
        return row[0] if row and row[0] is not None else None

    def delete_chat(self, chat_id: int) -> int:
        """Delete all messages for a chat. Returns number of deleted rows."""
        cursor = self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        self.conn.commit()
        return cursor.rowcount

    def top_senders(
        self,
        chat_id: int | None = None,
        hours: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get most active senders ranked by message count."""
        conditions = ["(sender_id IS NOT NULL OR sender_name IS NOT NULL)"]
        params: list[Any] = []
        if chat_id is not None:
            conditions.append("chat_id = ?")
            params.append(chat_id)
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)

        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"""SELECT MAX(sender_name) as sender_name, sender_id, COUNT(*) as msg_count,
                       MIN(timestamp) as first_msg, MAX(timestamp) as last_msg
                FROM messages WHERE {where}
                GROUP BY COALESCE(CAST(sender_id AS TEXT), 'name:' || COALESCE(sender_name, ''))
                ORDER BY msg_count DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def timeline(
        self,
        chat_id: int | None = None,
        hours: int | None = None,
        granularity: str = "day",
    ) -> list[dict]:
        """Get message count grouped by time period."""
        if granularity == "hour":
            time_expr = "substr(timestamp, 1, 13)"  # YYYY-MM-DDTHH
        else:
            time_expr = "substr(timestamp, 1, 10)"  # YYYY-MM-DD

        conditions = ["1=1"]
        params: list[Any] = []
        if chat_id is not None:
            conditions.append("chat_id = ?")
            params.append(chat_id)
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)

        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"""SELECT {time_expr} as period, COUNT(*) as msg_count
                FROM messages WHERE {where}
                GROUP BY period
                ORDER BY period ASC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────── fork additions: brief / links / thread / style ───────────────────

    def brief(self, chat_id: int) -> dict:
        """Chat passport: volume, activity, top senders, attachments, links."""
        totals = self.conn.execute(
            "SELECT COUNT(*) AS total, MIN(timestamp) AS first_msg, MAX(timestamp) AS last_msg"
            " FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()

        def _count_since(days: int) -> int:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            return self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND timestamp >= ?",
                (chat_id, cutoff),
            ).fetchone()[0]

        top_days = self.conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS msg_count"
            " FROM messages WHERE chat_id = ? GROUP BY day ORDER BY msg_count DESC LIMIT 3",
            (chat_id,),
        ).fetchall()
        attachments = self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM attachments WHERE chat_id = ? GROUP BY kind",
            (chat_id,),
        ).fetchall()
        links = self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM links WHERE chat_id = ? GROUP BY kind",
            (chat_id,),
        ).fetchall()
        return {
            "total": totals["total"],
            "first_msg": totals["first_msg"],
            "last_msg": totals["last_msg"],
            "msgs_7d": _count_since(7),
            "msgs_30d": _count_since(30),
            "top_days": [dict(r) for r in top_days],
            "top_senders": self.top_senders(chat_id=chat_id, limit=5),
            "attachments": {r["kind"]: r["n"] for r in attachments},
            "links": {r["kind"]: r["n"] for r in links},
        }

    def get_links(
        self,
        chat_id: int | None = None,
        hours: int | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Shared links, newest first, with message context."""
        query = """SELECT l.chat_id, l.msg_id, l.url, l.fetch_url, l.kind,
                          m.timestamp, m.sender_name, m.chat_name,
                          substr(coalesce(m.content, ''), 1, 160) AS snippet
                   FROM links l
                   LEFT JOIN messages m ON m.chat_id = l.chat_id AND m.msg_id = l.msg_id
                   WHERE 1=1"""
        params: list[Any] = []
        if chat_id is not None:
            query += " AND l.chat_id = ?"
            params.append(chat_id)
        if kind is not None:
            query += " AND l.kind = ?"
            params.append(kind)
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query += " AND m.timestamp >= ?"
            params.append(cutoff)
        query += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_attachments(
        self,
        chat_id: int | None = None,
        hours: int | None = None,
        kind: str | None = None,
        limit: int = 200,
        only_pending: bool = False,
    ) -> list[dict]:
        """Attachment metadata, newest first. only_pending → not yet downloaded."""
        query = """SELECT a.*, m.timestamp, m.sender_name, m.chat_name,
                          substr(coalesce(m.content, ''), 1, 120) AS snippet
                   FROM attachments a
                   LEFT JOIN messages m ON m.chat_id = a.chat_id AND m.msg_id = a.msg_id
                   WHERE 1=1"""
        params: list[Any] = []
        if chat_id is not None:
            query += " AND a.chat_id = ?"
            params.append(chat_id)
        if kind is not None:
            query += " AND a.kind = ?"
            params.append(kind)
        if hours is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query += " AND m.timestamp >= ?"
            params.append(cutoff)
        if only_pending:
            query += " AND a.local_path IS NULL"
        query += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def mark_attachment_downloaded(
        self,
        chat_id: int,
        msg_id: int,
        *,
        local_path: str,
        sha256: str,
        text_path: str | None = None,
        file_name: str | None = None,
    ) -> None:
        self.conn.execute(
            """UPDATE attachments
               SET local_path = ?, sha256 = ?, text_path = ?,
                   file_name = COALESCE(?, file_name),
                   downloaded_at = ?
               WHERE chat_id = ? AND msg_id = ?""",
            (
                local_path,
                sha256,
                text_path,
                file_name,
                datetime.now(timezone.utc).isoformat(),
                chat_id,
                msg_id,
            ),
        )
        self.conn.commit()

    def get_thread(self, chat_id: int, msg_id: int, max_hops: int = 200) -> list[dict]:
        """Reconstruct a reply thread: climb to the root, then collect all
        descendants, returned in chronological order."""
        root_id = msg_id
        for _ in range(max_hops):
            row = self.conn.execute(
                "SELECT reply_to_msg_id FROM messages WHERE chat_id = ? AND msg_id = ?",
                (chat_id, root_id),
            ).fetchone()
            parent = row["reply_to_msg_id"] if row else None
            if not parent:
                break
            # Follow even into a parent that was never synced (poll, service
            # message, out of sync window): it becomes a *virtual* root, so
            # sibling replies to it are still collected below.
            root_id = parent
            exists = self.conn.execute(
                "SELECT 1 FROM messages WHERE chat_id = ? AND msg_id = ?",
                (chat_id, parent),
            ).fetchone()
            if not exists:
                break  # cannot climb past a gap in the local cache

        rows = self.conn.execute(
            """WITH RECURSIVE thread(mid) AS (
                   VALUES(?)
                   UNION
                   SELECT m.msg_id FROM messages m
                   JOIN thread t ON m.reply_to_msg_id = t.mid
                   WHERE m.chat_id = ?
               )
               SELECT m.* FROM messages m
               JOIN thread t ON m.msg_id = t.mid
               WHERE m.chat_id = ?
               ORDER BY m.timestamp ASC""",
            (root_id, chat_id, chat_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─────────────────── marked-ID migration (#21) ───────────────────

    def has_chat(self, chat_id: int) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM messages WHERE chat_id = ? LIMIT 1", (chat_id,)
            ).fetchone()
            is not None
        )

    def remap_chat_id(self, old_id: int, new_id: int) -> int:
        """Move all rows of a chat to a new key (legacy bare → marked ID).

        Rows whose (chat_id, msg_id) already exist under the new key are
        dropped as duplicates — nothing is merged across different peers,
        because the caller derives new_id from the resolved entity itself.
        Returns the number of migrated message rows.
        """
        moved = 0
        with self.conn:  # single transaction
            cur = self.conn.execute(
                "UPDATE OR IGNORE messages SET chat_id = ? WHERE chat_id = ?",
                (new_id, old_id),
            )
            moved = max(cur.rowcount, 0)
            for table in ("attachments", "links", "sync_gaps"):
                self.conn.execute(
                    f"UPDATE OR IGNORE {table} SET chat_id = ? WHERE chat_id = ?",
                    (new_id, old_id),
                )
                self.conn.execute(f"DELETE FROM {table} WHERE chat_id = ?", (old_id,))
            # Leftover message rows are duplicates already present under new_id;
            # the FTS delete-trigger cleans their index entries.
            self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (old_id,))
        return moved

    # ─────────────────── sync gap cursors (#22) ───────────────────

    def record_gap(self, chat_id: int, from_id: int, to_id: int, kind: str = "gap") -> bool:
        """Remember an unfetched message range (from_id, to_id), both exclusive.

        Returns True when a non-empty range was recorded.
        """
        if to_id - from_id <= 1:
            return False
        return self.record_gap_id(chat_id, from_id, to_id, kind) is not None

    def record_gap_id(
        self, chat_id: int, from_id: int, to_id: int, kind: str = "gap"
    ) -> int | None:
        """record_gap returning the row id (for live cursor updates)."""
        if to_id - from_id <= 1:
            return None
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO sync_gaps (chat_id, from_id, to_id, kind, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (chat_id, from_id, to_id, kind, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        if cur.lastrowid and cur.rowcount > 0:
            return cur.lastrowid
        row = self.conn.execute(
            "SELECT id FROM sync_gaps WHERE chat_id=? AND from_id=? AND to_id=? AND kind=?",
            (chat_id, from_id, to_id, kind),
        ).fetchone()
        return row["id"] if row else None

    def get_gaps(self, chat_id: int | None = None, kind: str | None = None) -> list[dict]:
        query = "SELECT * FROM sync_gaps WHERE 1=1"
        params: list[Any] = []
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY chat_id, to_id DESC"
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def shrink_gap(self, gap_id: int, new_to_id: int) -> None:
        """Lower a gap's upper bound after a partial fill; delete when empty."""
        row = self.conn.execute(
            "SELECT from_id FROM sync_gaps WHERE id = ?", (gap_id,)
        ).fetchone()
        if row is None:
            return
        if new_to_id - row["from_id"] <= 1:
            self.conn.execute("DELETE FROM sync_gaps WHERE id = ?", (gap_id,))
        else:
            self.conn.execute(
                "UPDATE sync_gaps SET to_id = ? WHERE id = ?", (new_to_id, gap_id)
            )
        self.conn.commit()

    def delete_gap(self, gap_id: int) -> None:
        self.conn.execute("DELETE FROM sync_gaps WHERE id = ?", (gap_id,))
        self.conn.commit()

    def count_gaps(self, kind: str = "gap", chat_id: int | None = None) -> int:
        query = "SELECT COUNT(*) FROM sync_gaps WHERE kind = ?"
        params: list[Any] = [kind]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        return self.conn.execute(query, params).fetchone()[0]

    def get_style_corpus(
        self,
        sender_id: int,
        chat_id: int | None = None,
        limit: int = 500,
        min_len: int = 15,
    ) -> list[dict]:
        """My own messages, newest first — the corpus for style calibration."""
        query = (
            "SELECT content, chat_name, timestamp FROM messages"
            " WHERE sender_id = ? AND content IS NOT NULL"
            " AND length(content) >= ? AND content NOT LIKE '/%'"
        )
        params: list[Any] = [sender_id, min_len]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def close(self):
        self.conn.close()
