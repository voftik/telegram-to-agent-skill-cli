"""Tests for fork query additions: FTS search, brief, links, thread, style."""

from __future__ import annotations

from conftest import make_msg

# ─────────────────────── FTS search ───────────────────────


class TestFtsSearch:
    def test_prefix_matches_wordforms(self, db):
        db.insert_message(**make_msg(msg_id=1, content="мы договорились о сроках"))
        db.insert_message(**make_msg(msg_id=2, content="жду договорённость по бюджету"))
        db.insert_message(**make_msg(msg_id=3, content="совсем о другом"))
        results = db.search("договор*")
        assert {r["msg_id"] for r in results} == {1, 2}

    def test_substring_fallback_when_fts_misses(self, db):
        # FTS tokenizes "проекта" as one term; bare "проект" matches only
        # via the LIKE fallback.
        db.insert_message(**make_msg(msg_id=1, content="обсуждение проекта"))
        results = db.search("проект")
        assert [r["msg_id"] for r in results] == [1]

    def test_broken_fts_syntax_falls_back(self, db):
        db.insert_message(**make_msg(msg_id=1, content='строка с "кавычкой (и скобкой'))
        results = db.search('"кавычкой (и')
        assert [r["msg_id"] for r in results] == [1]

    def test_chat_filter(self, db):
        db.insert_message(**make_msg(chat_id=1, msg_id=1, content="запуск завтра"))
        db.insert_message(**make_msg(chat_id=2, msg_id=1, content="запуск отменён"))
        results = db.search("запуск", chat_id=1)
        assert len(results) == 1
        assert results[0]["chat_id"] == 1


# ─────────────────────── brief ───────────────────────


class TestBrief:
    def test_brief_counts(self, db):
        db.insert_message(**make_msg(msg_id=1, content="старое", hours_ago=24 * 40))
        db.insert_message(**make_msg(msg_id=2, content="недавнее", hours_ago=2))
        db.insert_attachments(
            [
                dict(chat_id=100, msg_id=2, kind="document",
                     file_name="a.pdf", mime_type="application/pdf", size_bytes=1),
            ]
        )
        db.insert_links(
            [dict(chat_id=100, msg_id=2, url="https://a.io", fetch_url="https://a.io", kind="web")]
        )
        info = db.brief(100)
        assert info["total"] == 2
        assert info["msgs_7d"] == 1
        assert info["msgs_30d"] == 1
        assert info["attachments"] == {"document": 1}
        assert info["links"] == {"web": 1}
        assert len(info["top_days"]) == 2


# ─────────────────────── links / attachments queries ───────────────────────


class TestLinkQueries:
    def test_get_links_joins_context(self, db):
        db.insert_message(**make_msg(msg_id=7, content="вот док https://docs.google.com/..."))
        db.insert_links(
            [
                dict(
                    chat_id=100,
                    msg_id=7,
                    url="https://docs.google.com/document/d/x/edit",
                    fetch_url="https://docs.google.com/document/d/x/export?format=txt",
                    kind="gdoc",
                )
            ]
        )
        rows = db.get_links(chat_id=100)
        assert len(rows) == 1
        assert rows[0]["kind"] == "gdoc"
        assert rows[0]["sender_name"] == "Alice"
        assert "export?format=txt" in rows[0]["fetch_url"]

    def test_kind_filter(self, db):
        db.insert_message(**make_msg(msg_id=1))
        db.insert_links(
            [
                dict(chat_id=100, msg_id=1, url="https://a.io", fetch_url="https://a.io",
                     kind="web"),
                dict(chat_id=100, msg_id=1, url="https://t.me/c/1/2", fetch_url=None,
                     kind="tme"),
            ]
        )
        assert len(db.get_links(kind="web")) == 1

    def test_get_attachments_pending(self, db):
        db.insert_message(**make_msg(msg_id=1, has_media=True))
        db.insert_attachments(
            [dict(chat_id=100, msg_id=1, kind="document",
                  file_name="b.docx", mime_type=None, size_bytes=5)]
        )
        assert len(db.get_attachments(only_pending=True)) == 1
        db.mark_attachment_downloaded(
            100, 1, local_path="/tmp/b.docx", sha256="ff", text_path="/tmp/b.docx.txt"
        )
        assert db.get_attachments(only_pending=True) == []
        row = db.get_attachments(chat_id=100)[0]
        assert row["sha256"] == "ff"
        assert row["downloaded_at"] is not None


# ─────────────────────── thread ───────────────────────


class TestThread:
    def _chain(self, db):
        # 1 ← 2 ← 3, and 4 standalone
        db.insert_message(**make_msg(msg_id=1, content="корень", hours_ago=3))
        db.insert_message(**make_msg(msg_id=2, content="ответ", hours_ago=2, reply_to_msg_id=1))
        db.insert_message(**make_msg(msg_id=3, content="ещё", hours_ago=1, reply_to_msg_id=2))
        db.insert_message(**make_msg(msg_id=4, content="мимо", hours_ago=1))

    def test_thread_from_middle(self, db):
        self._chain(db)
        msgs = db.get_thread(100, 2)
        assert [m["msg_id"] for m in msgs] == [1, 2, 3]

    def test_thread_from_leaf(self, db):
        self._chain(db)
        assert [m["msg_id"] for m in db.get_thread(100, 3)] == [1, 2, 3]

    def test_thread_missing_parent_collects_siblings(self, db):
        # Two replies to a message that was never synced (poll/service msg):
        # the missing parent acts as a virtual root, siblings are collected.
        db.insert_message(
            **make_msg(msg_id=10, content="сирота раз", hours_ago=2, reply_to_msg_id=999)
        )
        db.insert_message(
            **make_msg(msg_id=11, content="сирота два", hours_ago=1, reply_to_msg_id=999)
        )
        msgs = db.get_thread(100, 10)
        assert [m["msg_id"] for m in msgs] == [10, 11]


# ─────────────────────── style corpus ───────────────────────


class TestStyleCorpus:
    def test_filters_short_and_commands(self, db):
        db.insert_message(**make_msg(msg_id=1, sender_id=7, content="ок"))
        db.insert_message(**make_msg(msg_id=2, sender_id=7, content="/start у бота"))
        db.insert_message(
            **make_msg(msg_id=3, sender_id=7, content="длинное содержательное сообщение")
        )
        db.insert_message(
            **make_msg(msg_id=4, sender_id=8, content="чужое сообщение достаточной длины")
        )
        corpus = db.get_style_corpus(sender_id=7)
        assert [c["content"] for c in corpus] == ["длинное содержательное сообщение"]


# ─────────────────────── marked peer IDs (#21) ───────────────────────


class TestMarkedIds:
    def test_marked_id_for_tl_peers(self):
        from telethon.tl.types import PeerChannel, PeerChat, PeerUser

        from tg_cli.client import marked_peer_id

        assert marked_peer_id(PeerUser(123)) == 123
        assert marked_peer_id(PeerChat(1005)) == -1005
        assert marked_peer_id(PeerChannel(123)) == -1000000000123

    def test_user_and_channel_with_same_bare_id_stay_separate(self, db):
        db.insert_message(**make_msg(chat_id=123, chat_name="Пользователь", msg_id=1))
        db.insert_message(
            **make_msg(chat_id=-1000000000123, chat_name="Канал", msg_id=1)
        )
        chats = db.get_chats()
        assert len(chats) == 2

    def test_numeric_lookup_matches_any_marked_form(self, db):
        db.insert_message(
            **make_msg(chat_id=-1001307778786, chat_name="Эксплойт", msg_id=1)
        )
        assert db.resolve_chat_id("1307778786") == -1001307778786
        assert db.resolve_chat_id("-1001307778786") == -1001307778786

    def test_basic_group_negative_id_resolves(self, db):
        db.insert_message(**make_msg(chat_id=-1005, chat_name="Группа", msg_id=1))
        assert db.resolve_chat_id("-1005") == -1005
        assert db.resolve_chat_id("1005") == -1005

    def test_remap_moves_all_tables(self, db):
        db.insert_message(**make_msg(chat_id=555, chat_name="Legacy", msg_id=1))
        db.insert_message(**make_msg(chat_id=555, chat_name="Legacy", msg_id=2))
        db.insert_attachments(
            [dict(chat_id=555, msg_id=2, kind="document",
                  file_name="a.pdf", mime_type=None, size_bytes=1)]
        )
        db.insert_links(
            [dict(chat_id=555, msg_id=1, url="https://a.io", fetch_url="https://a.io",
                  kind="web")]
        )
        db.record_gap(555, 0, 100, kind="backfill")

        moved = db.remap_chat_id(555, -1000000000555)
        assert moved == 2
        assert db.has_chat(555) is False
        assert db.count(chat_id=-1000000000555) == 2
        assert len(db.get_attachments(chat_id=-1000000000555)) == 1
        assert len(db.get_links(chat_id=-1000000000555)) == 1
        assert len(db.get_gaps(chat_id=-1000000000555, kind="backfill")) == 1
        # FTS follows the moved rows
        hits = db.conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'Hello'"
        ).fetchall()
        assert len(hits) == 2

    def test_remap_drops_duplicates_without_losing_new_rows(self, db):
        db.insert_message(**make_msg(chat_id=555, msg_id=1, content="старая копия"))
        db.insert_message(**make_msg(chat_id=-1000000000555, msg_id=1, content="новая"))
        db.insert_message(**make_msg(chat_id=-1000000000555, msg_id=2, content="ещё"))
        db.remap_chat_id(555, -1000000000555)
        rows = db.conn.execute(
            "SELECT msg_id, content FROM messages WHERE chat_id = -1000000000555"
            " ORDER BY msg_id"
        ).fetchall()
        assert [(r["msg_id"], r["content"]) for r in rows] == [(1, "новая"), (2, "ещё")]


# ─────────────────────── atomic batches (#23) ───────────────────────


class TestAtomicStoreBatch:
    def test_bad_row_rolls_back_whole_batch(self, db):
        import sqlite3

        import pytest as _pytest

        msgs = [make_msg(msg_id=i, content=f"msg {i}") for i in range(1, 4)]
        bad_attachment = dict(
            chat_id=100, msg_id=2, kind=None,  # NOT NULL violation
            file_name="x", mime_type=None, size_bytes=1,
        )
        with _pytest.raises(sqlite3.Error):
            db.store_batch(msgs, [bad_attachment], [])
        assert db.count() == 0  # nothing from the batch survived

    def test_error_not_silently_zero(self, db):
        """insert_message must raise on real errors, not return False (#23)."""
        import sqlite3

        import pytest as _pytest

        db.conn.execute("DROP TABLE attachments")
        with _pytest.raises(sqlite3.Error):
            db.insert_attachments(
                [dict(chat_id=1, msg_id=1, kind="document",
                      file_name=None, mime_type=None, size_bytes=None)]
            )

    def test_failed_flush_marks_sync_failed_and_records_gap(self, db, monkeypatch):
        """A storage error mid-sync → status failed + gap cursor (recovery
        path: the range will be re-fetched, restoring attachments/links)."""
        import asyncio
        import sqlite3 as _sq

        from test_client import _make_chat

        from tg_cli.client import fetch_history

        client, entity = _make_chat(450)
        original = db.store_batch
        calls = {"n": 0}

        def flaky(messages, attachments=None, links=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise _sq.OperationalError("disk I/O error")
            return original(messages, attachments, links)

        monkeypatch.setattr(db, "store_batch", flaky)
        res = asyncio.run(fetch_history(client, 100, db=db, limit=1000, min_id=0))
        assert res["status"] == "failed"
        assert db.get_gaps(chat_id=100) != []


# ─────────────────────── edits & deletes (#24) ───────────────────────


class TestEditsAndDeletes:
    def test_upsert_refreshes_content_fts_and_links(self, db):
        db.store_batch(
            [make_msg(msg_id=1, content="устаревший текст http://old.io")],
            [],
            [dict(chat_id=100, msg_id=1, url="http://old.io",
                  fetch_url="http://old.io", kind="web")],
        )
        # Refetch after remote edit
        db.store_batch(
            [make_msg(msg_id=1, content="исправленный текст http://new.io")],
            [],
            [dict(chat_id=100, msg_id=1, url="http://new.io",
                  fetch_url="http://new.io", kind="web")],
        )
        row = db.conn.execute(
            "SELECT content FROM messages WHERE msg_id = 1"
        ).fetchone()
        assert row["content"] == "исправленный текст http://new.io"
        assert db.search("исправленный") != []
        assert db.search("устаревший") == []
        urls = [r["url"] for r in db.get_links(chat_id=100)]
        assert urls == ["http://new.io"]

    def test_delete_messages_cascades(self, db, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"x")
        db.store_batch(
            [make_msg(msg_id=1, content="удаляемое", has_media=True)],
            [dict(chat_id=100, msg_id=1, kind="document",
                  file_name="doc.pdf", mime_type=None, size_bytes=1)],
            [dict(chat_id=100, msg_id=1, url="https://a.io",
                  fetch_url="https://a.io", kind="web")],
        )
        db.mark_attachment_downloaded(
            100, 1, local_path=str(f), sha256="aa", text_path=None
        )
        res = db.delete_messages(100, [1])
        assert res["messages"] == 1
        assert res["attachments"] == 1
        assert res["links"] == 1
        assert str(f) in res["files"]
        assert db.search("удаляемое") == []
        assert db.get_links(chat_id=100) == []
        assert db.get_attachments(chat_id=100) == []


# ─────────────────────── migration safety (#25) ───────────────────────


class TestMigrationSafety:
    def test_concurrent_opens_of_v1_db(self, tmp_path):
        import sqlite3
        import threading

        from tg_cli.db import MessageDB

        path = tmp_path / "v1.db"
        conn = sqlite3.connect(str(path))
        conn.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL DEFAULT 'telegram',
                chat_id INTEGER NOT NULL, chat_name TEXT, msg_id INTEGER NOT NULL,
                sender_id INTEGER, sender_name TEXT, content TEXT,
                timestamp TEXT NOT NULL, raw_json TEXT,
                UNIQUE(platform, chat_id, msg_id))"""
        )
        conn.execute(
            "INSERT INTO messages (chat_id, msg_id, content, timestamp)"
            " VALUES (1, 1, 'параллельная миграция', 't')"
        )
        conn.commit()
        conn.close()

        errors: list[Exception] = []

        def _open():
            try:
                MessageDB(path).close()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=_open) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

        db = MessageDB(path)
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.search("параллельная") != []
        db.close()

    def test_fts_desync_repaired_on_open(self, tmp_path):
        from tg_cli.db import MessageDB

        path = tmp_path / "m.db"
        db = MessageDB(path)
        db.insert_message(**make_msg(msg_id=1, content="потерянный из индекса"))
        # Simulate a crash that left the row unindexed
        row_id = db.conn.execute("SELECT id FROM messages WHERE msg_id = 1").fetchone()[0]
        db.conn.execute(
            "INSERT INTO messages_fts(messages_fts, rowid, content)"
            " VALUES ('delete', ?, 'потерянный из индекса')",
            (row_id,),
        )
        db.conn.commit()
        assert db._search_fts("потерянный", None, None, None, 10) == []
        db.close()

        db2 = MessageDB(path)  # reopen → integrity check → rebuild
        assert db2._search_fts("потерянный", None, None, None, 10) != []
        db2.close()
