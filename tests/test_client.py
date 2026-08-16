"""Tests for Telegram client helpers without hitting the network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from conftest import make_msg

from tg_cli.client import fetch_history, sync_all


@dataclass
class FakeEntity:
    id: int
    title: str


@dataclass
class FakeDialog:
    entity: FakeEntity
    name: str


@dataclass
class FakeSender:
    id: int
    first_name: str = "User"
    last_name: str = ""
    username: str | None = None


@dataclass
class FakeMessage:
    id: int
    sender_id: int
    text: str
    date: datetime
    message: str | None = None
    _sender: object = None

    def __post_init__(self):
        if self._sender is None:
            self._sender = FakeSender(id=self.sender_id)


class FakeClient:
    def __init__(self, dialogs: list[FakeDialog], messages_by_chat: dict[int, list[FakeMessage]]):
        self._dialogs = dialogs
        self._messages_by_chat = messages_by_chat
        self.fail_after: int | None = None  # raise mid-iteration after N messages
        self.fail_dialogs = False           # make iter_dialogs raise

    async def _noop(self):
        return None

    async def get_entity(self, chat):
        if isinstance(chat, FakeEntity):
            return chat
        for dialog in self._dialogs:
            if chat == dialog.entity.id or chat == dialog.name:
                return dialog.entity
        raise ValueError(f"unknown chat: {chat}")

    async def iter_dialogs(self):
        if self.fail_dialogs:
            raise RuntimeError("network unreachable")
        for dialog in self._dialogs:
            yield dialog

    async def iter_messages(self, entity, limit: int, min_id: int = 0, max_id: int = 0):
        # Realistic Telegram semantics: newest-first, min_id/max_id exclusive,
        # limit applied after range filtering.
        messages = sorted(self._messages_by_chat.get(entity.id, []), key=lambda m: -m.id)
        yielded = 0
        for msg in messages:
            if yielded >= limit:
                break
            if msg.id <= min_id:
                continue
            if max_id and msg.id >= max_id:
                continue
            if self.fail_after is not None and yielded >= self.fail_after:
                raise RuntimeError("connection dropped")
            yield msg
            yielded += 1


@pytest.mark.asyncio
async def test_fetch_history_returns_inserted_count(db):
    entity = FakeEntity(id=100, title="Test Group")
    client = FakeClient(
        dialogs=[FakeDialog(entity=entity, name="Test Group")],
        messages_by_chat={
            100: [
                FakeMessage(id=1, sender_id=1, text="old", date=datetime.now(timezone.utc)),
                FakeMessage(id=2, sender_id=1, text="new-1", date=datetime.now(timezone.utc)),
                FakeMessage(id=3, sender_id=1, text="new-2", date=datetime.now(timezone.utc)),
            ]
        },
    )

    db.insert_message(
        chat_id=100,
        chat_name="Test Group",
        msg_id=1,
        sender_id=1,
        sender_name="Alice",
        content="old",
        timestamp=datetime.now(timezone.utc),
    )

    res = await fetch_history(client, 100, db=db, limit=10, batch_delay=0)
    assert res["stored"] == 2
    assert res["status"] == "complete"
    assert res["error"] is None


@pytest.mark.asyncio
async def test_sync_all_discovers_dialogs_from_client(db):
    dialogs = [
        FakeDialog(entity=FakeEntity(id=100, title="Group A"), name="Group A"),
        FakeDialog(entity=FakeEntity(id=200, title="Group B"), name="Group B"),
    ]
    client = FakeClient(
        dialogs=dialogs,
        messages_by_chat={
            100: [FakeMessage(id=1, sender_id=1, text="hello", date=datetime.now(timezone.utc))],
            200: [FakeMessage(id=1, sender_id=2, text="world", date=datetime.now(timezone.utc))],
        },
    )

    report = await sync_all(client, db, limit_per_chat=10, delay=0)
    assert report["enumerated"] is True
    assert report["total"] == 2
    assert report["ok"] == 2
    assert report["failed"] == 0
    assert report["new_messages"] == 2
    assert set(report["results"]) == {100, 200}
    assert report["results"][100]["name"] == "Group A"
    assert db.count() == 2


@pytest.mark.asyncio
async def test_sync_all_max_chats_limits_synced_dialogs(db):
    dialogs = [
        FakeDialog(entity=FakeEntity(id=100, title="Group A"), name="Group A"),
        FakeDialog(entity=FakeEntity(id=200, title="Group B"), name="Group B"),
        FakeDialog(entity=FakeEntity(id=300, title="Group C"), name="Group C"),
    ]
    client = FakeClient(
        dialogs=dialogs,
        messages_by_chat={
            100: [FakeMessage(id=1, sender_id=1, text="hello", date=datetime.now(timezone.utc))],
            200: [FakeMessage(id=1, sender_id=2, text="world", date=datetime.now(timezone.utc))],
            300: [FakeMessage(id=1, sender_id=3, text="bye", date=datetime.now(timezone.utc))],
        },
    )

    report = await sync_all(client, db, limit_per_chat=10, delay=0, max_chats=1)
    assert report["total"] == 1
    assert db.count() == 1


@pytest.mark.asyncio
async def test_connect_uses_default_credentials_when_env_unset(monkeypatch):
    """When TG_API_ID/TG_API_HASH are not set, connect() should use Telegram Desktop defaults."""
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)

    from tg_cli.config import get_api_hash, get_api_id

    api_id = get_api_id()
    api_hash = get_api_hash()
    # Defaults should be set (Telegram Desktop credentials)
    assert api_id is not None
    assert api_hash is not None
    assert isinstance(api_id, int)
    assert len(api_hash) > 0


# ─────────────────────── safe attachment filenames (#20) ───────────────────────


class TestSafeAttachmentFilename:
    def _name(self, raw, msg_id=7, kind="document", mime="application/pdf"):
        from tg_cli.client import safe_attachment_filename

        return safe_attachment_filename(msg_id, raw, kind, mime)

    def test_plain_name_kept(self):
        assert self._name("отчёт.pdf") == "7_отчёт.pdf"

    def test_posix_traversal_stripped(self):
        assert self._name("dir/../../../escaped.bin") == "7_escaped.bin"

    def test_windows_traversal_stripped(self):
        assert self._name("..\\..\\evil.exe") == "7_evil.exe"

    def test_absolute_path_stripped(self):
        assert self._name("/etc/passwd") == "7_passwd"

    def test_dotfiles_and_relative_prefix(self):
        assert self._name("...hidden") == "7_hidden"
        assert self._name("..") == "7_document_7.pdf"

    def test_windows_reserved_name_replaced(self):
        assert self._name("CON.txt") == "7_document_7.pdf"
        assert self._name("com1.anything") == "7_document_7.pdf"

    def test_control_chars_replaced(self):
        assert self._name("bad\x00name\n.txt") == "7_bad_name_.txt"

    def test_empty_name_fallback(self):
        assert self._name(None) == "7_document_7.pdf"
        assert self._name("   ") == "7_document_7.pdf"

    def test_unicode_kept(self):
        assert self._name("смета 2026 §7.xlsx") == "7_смета 2026 §7.xlsx"

    def test_length_capped(self):
        assert len(self._name("x" * 500)) <= 200


# ─────────────────────── gap-safe sync (#22) ───────────────────────


def _make_chat(n_messages: int, chat_id: int = 100, name: str = "Big Chat"):
    entity = FakeEntity(id=chat_id, title=name)
    msgs = [
        FakeMessage(id=i, sender_id=1, text=f"msg {i}", date=datetime.now(timezone.utc))
        for i in range(1, n_messages + 1)
    ]
    client = FakeClient(
        dialogs=[FakeDialog(entity=entity, name=name)],
        messages_by_chat={chat_id: msgs},
    )
    return client, entity


class TestGapSafeSync:
    @pytest.mark.asyncio
    async def test_backlog_over_limit_records_gap_and_heals(self, db):
        """Server range 101..500 over local checkpoint 100 with limit=200:
        the un-fetched middle must be recorded and later healed — the exact
        scenario that used to lose 101..300 forever."""
        from tg_cli.client import fill_gaps

        client, entity = _make_chat(500)
        db.insert_message(**make_msg(chat_id=100, msg_id=100, content="checkpoint"))

        res = await fetch_history(client, 100, db=db, limit=200, min_id=100)
        assert res["stored"] == 200          # newest 301..500
        assert res["status"] == "partial"
        gaps = db.get_gaps(chat_id=100, kind="gap")
        assert len(gaps) == 1
        assert gaps[0]["from_id"] == 100
        assert gaps[0]["to_id"] == 301

        # Heal in two limited passes — no hole may remain.
        r1 = await fill_gaps(client, db, 100, limit=150)
        assert r1["error"] is None
        r2 = await fill_gaps(client, db, 100, limit=150)
        assert r2["remaining"] == 0
        stored_ids = {
            r["msg_id"]
            for r in db.conn.execute(
                "SELECT msg_id FROM messages WHERE chat_id = 100"
            ).fetchall()
        }
        assert stored_ids == set(range(100, 501))

    @pytest.mark.asyncio
    async def test_interrupted_fetch_records_gap(self, db):
        """A connection drop after the first committed batch must leave a
        cursor, not a silent hole."""
        from tg_cli.client import fill_gaps

        client, entity = _make_chat(450)
        client.fail_after = 250  # dies mid-iteration

        res = await fetch_history(client, 100, db=db, limit=1000, min_id=0)
        assert res["status"] == "failed"
        assert res["stored"] > 0
        gaps = db.get_gaps(chat_id=100)
        assert len(gaps) == 1

        client.fail_after = None
        r = await fill_gaps(client, db, 100, kind=gaps[0]["kind"], limit=1000)
        assert r["remaining"] == 0
        assert db.count(chat_id=100) == 450

    @pytest.mark.asyncio
    async def test_first_sync_cap_records_backfill_not_gap(self, db):
        client, entity = _make_chat(800)
        res = await fetch_history(client, 100, db=db, limit=500, min_id=0)
        assert res["stored"] == 500
        assert res["status"] == "complete"   # by-design depth cap, not a hole
        assert db.count_gaps(kind="gap", chat_id=100) == 0
        backfills = db.get_gaps(chat_id=100, kind="backfill")
        assert len(backfills) == 1
        assert backfills[0]["to_id"] == 301  # oldest fetched was 301

    @pytest.mark.asyncio
    async def test_backfill_walks_history_to_the_start(self, db):
        from tg_cli.client import fill_gaps

        client, entity = _make_chat(800)
        await fetch_history(client, 100, db=db, limit=500, min_id=0)
        r1 = await fill_gaps(client, db, 100, kind="backfill", limit=200)
        assert r1["stored"] == 200
        assert r1["remaining"] == 1          # shrunk, not closed
        r2 = await fill_gaps(client, db, 100, kind="backfill", limit=200)
        assert r2["remaining"] == 0
        assert db.count(chat_id=100) == 800

    @pytest.mark.asyncio
    async def test_complete_pass_records_nothing(self, db):
        client, entity = _make_chat(50)
        res = await fetch_history(client, 100, db=db, limit=500, min_id=0)
        assert res["status"] == "complete"
        assert db.get_gaps(chat_id=100) == []


# ─────────────────────── sync_all pass report (#19) ───────────────────────


class TestSyncAllReport:
    @pytest.mark.asyncio
    async def test_enumeration_failure_is_not_success(self, db):
        client, _ = _make_chat(5)
        client.fail_dialogs = True
        report = await sync_all(client, db, limit_per_chat=10, delay=0)
        assert report["enumerated"] is False
        assert "enumeration failed" in report["error"]
        assert report["total"] == 0

    @pytest.mark.asyncio
    async def test_single_chat_failure_is_counted(self, db):
        dialogs = [
            FakeDialog(entity=FakeEntity(id=100, title="Good"), name="Good"),
            FakeDialog(entity=FakeEntity(id=200, title="Bad"), name="Bad"),
        ]
        client = FakeClient(
            dialogs=dialogs,
            messages_by_chat={
                100: [
                    FakeMessage(
                        id=1, sender_id=1, text="hi", date=datetime.now(timezone.utc)
                    )
                ],
            },
        )

        async def broken_iter(entity, limit, min_id=0, max_id=0):
            if entity.id == 200:
                raise RuntimeError("boom")
            async for m in FakeClient.iter_messages(client, entity, limit, min_id, max_id):
                yield m

        client.iter_messages = broken_iter
        report = await sync_all(client, db, limit_per_chat=10, delay=0)
        assert report["failed"] == 1
        assert report["ok"] == 1
        assert report["results"][200]["status"] == "failed"
