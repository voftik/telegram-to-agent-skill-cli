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
