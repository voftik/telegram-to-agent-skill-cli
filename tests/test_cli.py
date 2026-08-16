"""Tests for CLI commands — uses CliRunner with temp DB, no Telegram dependency."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tg_cli.cli.main import cli
from tg_cli.db import MessageDB


@pytest.fixture
def runner():
    return CliRunner()


class TestStats:
    def test_stats_output(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["stats"])
        assert result.exit_code == 0
        assert "TestGroup" in result.output
        assert "10" in result.output

    def test_stats_yaml(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["stats", "--yaml"])
        assert result.exit_code == 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert data["total"] == 10
        assert data["chats"][0]["chat_name"] == "TestGroup"

    def test_stats_auto_yaml_when_stdout_is_not_tty(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        monkeypatch.setenv("OUTPUT", "auto")
        result = runner.invoke(cli, ["stats"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)["data"]
        assert data["total"] == 10


class TestSearch:
    def test_search_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "Web3"])
        assert result.exit_code == 0
        assert "Web3" in result.output

    def test_search_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "nonexistent_keyword_xyz"])
        assert result.exit_code == 0
        assert "No messages found" in result.output

    def test_search_with_sender_and_hours(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "Web3", "--sender", "Alice", "--hours", "5"])
        assert result.exit_code == 0
        assert "Found 2 messages" in result.output
        assert "sender=Alice" in result.output
        assert "hours=5" in result.output

    def test_search_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "Web3", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output

    def test_search_chat_not_found_yaml(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "Web3", "--chat", "MissingGroup", "--yaml"])
        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "chat_not_found"

    def test_search_regex_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(
            cli,
            ["search", r"Message [12]: (Python|Web3)", "--regex", "--limit", "2"],
        )
        assert result.exit_code == 0
        assert "mode=regex" in result.output

    def test_search_regex_invalid(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "(", "--regex"])
        assert result.exit_code == 0
        assert "Invalid regex pattern" in result.output

    def test_search_yaml(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "Web3", "--yaml"])
        assert result.exit_code == 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert isinstance(data, list)
        assert data[0]["content"]


class TestRecent:
    def test_recent_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["recent", "--hours", "3", "--limit", "3"])
        assert result.exit_code == 0
        assert "Showing 2 recent messages" in result.output
        assert "hours=3" in result.output

    def test_recent_with_sender_filter(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["recent", "--sender", "Ali", "--hours", "5"])
        assert result.exit_code == 0
        assert "sender=Ali" in result.output

    def test_recent_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["recent", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output


class TestQueryChatNotFound:
    def test_today_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["today", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output

    def test_top_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["top", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output

    def test_timeline_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["timeline", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output

    def test_filter_chat_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["filter", "Web3", "--chat", "MissingGroup"])
        assert result.exit_code == 0
        assert "Chat 'MissingGroup' not found in database." in result.output


class TestTodayHints:
    def test_today_shows_refresh_hint_when_local_data_is_old(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        db = MessageDB(db_path=db_path)
        db.insert_message(
            chat_id=100,
            chat_name="OldGroup",
            msg_id=1,
            sender_id=1,
            sender_name="Alice",
            content="old message",
            timestamp=datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc),
        )

        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["today"])
        assert result.exit_code == 0
        assert "No messages today." in result.output
        assert "Latest local message is from" in result.output
        assert "Run 'tg refresh'" in result.output
        assert "refresh." in result.output

    def test_today_shows_empty_db_hint(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        MessageDB(db_path=db_path).close()

        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["today"])
        assert result.exit_code == 0
        assert "No messages today." in result.output
        assert "Local database is empty. Run 'tg refresh' first." in result.output


class TestRefreshAndSyncFirst:
    def test_refresh_yaml(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        async def fake_sync_all_dialogs(*, limit, on_chat_done=None, delay=1.0, max_chats=None):
            assert limit == 5000
            return {
                "enumerated": True,
                "error": None,
                "total": 2,
                "ok": 2,
                "partial": 0,
                "failed": 0,
                "new_messages": 2,
                "results": {
                    1: {"name": "ChatA", "new": 2, "status": "complete", "error": None},
                    2: {"name": "ChatB", "new": 0, "status": "complete", "error": None},
                },
            }

        monkeypatch.setattr(tg_mod, "sync_all_dialogs", fake_sync_all_dialogs)
        result = runner.invoke(cli, ["refresh", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)["data"]
        assert data["new_messages"] == 2
        assert data["failed"] == 0
        assert data["enumerated"] is True


class TestStatus:
    def _patch_auth(self, monkeypatch, payload):
        import tg_cli.client as client_mod

        async def fake_check_auth():
            return payload

        monkeypatch.setattr(client_mod, "check_auth", fake_check_auth)

    def test_status_yaml_authenticated(self, runner, monkeypatch):
        self._patch_auth(
            monkeypatch,
            {
                "authenticated": True,
                "reachable": True,
                "error": None,
                "id": 123,
                "first_name": "Alice",
                "last_name": "Smith",
                "username": "alice",
                "phone": "123456",
            },
        )
        result = runner.invoke(cli, ["status", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["ok"] is True
        assert data["data"]["authenticated"] is True
        assert data["data"]["user"]["username"] == "alice"

    def test_status_unauthorized_is_clean_exit(self, runner, monkeypatch):
        """No session → authenticated:false, exit 0, no prompt (#32)."""
        self._patch_auth(
            monkeypatch,
            {"authenticated": False, "reachable": True, "error": None},
        )
        result = runner.invoke(cli, ["status", "--yaml"], input="")
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["data"]["authenticated"] is False
        assert data["data"]["reachable"] is True
        assert "phone" not in result.output.lower()

    def test_status_network_error_distinguished(self, runner, monkeypatch):
        self._patch_auth(
            monkeypatch,
            {"authenticated": False, "reachable": False, "error": "dns fail"},
        )
        result = runner.invoke(cli, ["status", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["data"]["reachable"] is False
        assert data["data"]["error"] == "dns fail"

    def test_whoami_yaml(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMe:
            id = 123
            first_name = "Alice"
            last_name = "Smith"
            username = "alice"
            phone = "123456"

        class FakeClient:
            async def get_me(self):
                return FakeMe()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["whoami", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["ok"] is True
        assert data["schema_version"] == "1"
        assert data["data"]["user"]["username"] == "alice"
        assert data["data"]["user"]["name"] == "Alice Smith"

    def test_today_sync_first_refreshes_before_query(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        MessageDB(db_path=db_path).close()

        import tg_cli.cli.query as query_mod
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)

        async def fake_sync_all_dialogs(*, limit, on_chat_done=None):
            with MessageDB(db_path=db_path) as db:
                db.insert_message(
                    chat_id=100,
                    chat_name="FreshGroup",
                    msg_id=1,
                    sender_id=1,
                    sender_name="Alice",
                    content="new today",
                    timestamp=datetime.now(timezone.utc),
                )
            return {"FreshGroup": 1}

        monkeypatch.setattr(query_mod, "sync_all_dialogs", fake_sync_all_dialogs)
        result = runner.invoke(cli, ["today", "--sync-first"])
        assert result.exit_code == 0
        assert "FreshGroup" in result.output

    def test_search_sync_first_syncs_single_chat(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        MessageDB(db_path=db_path).close()

        import tg_cli.cli.query as query_mod
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)

        async def fake_sync_chat_dialog(chat, *, limit, on_progress=None):
            assert chat == "FreshGroup"
            with MessageDB(db_path=db_path) as db:
                db.insert_message(
                    chat_id=100,
                    chat_name="FreshGroup",
                    msg_id=1,
                    sender_id=1,
                    sender_name="Alice",
                    content="fresh web3 note",
                    timestamp=datetime.now(timezone.utc),
                )
            return 1

        monkeypatch.setattr(query_mod, "sync_chat_dialog", fake_sync_chat_dialog)
        result = runner.invoke(cli, ["search", "web3", "--chat", "FreshGroup", "--sync-first"])
        assert result.exit_code == 0
        assert "fresh web3 note" in result.output

    def test_stats_sync_first_refreshes_before_summary(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        MessageDB(db_path=db_path).close()

        import tg_cli.cli.query as query_mod
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)

        async def fake_sync_all_dialogs(*, limit, on_chat_done=None):
            with MessageDB(db_path=db_path) as db:
                db.insert_message(
                    chat_id=100,
                    chat_name="FreshGroup",
                    msg_id=1,
                    sender_id=1,
                    sender_name="Alice",
                    content="fresh web3 note",
                    timestamp=datetime.now(timezone.utc),
                )
            return {"FreshGroup": 1}

        monkeypatch.setattr(query_mod, "sync_all_dialogs", fake_sync_all_dialogs)
        result = runner.invoke(cli, ["stats", "--sync-first", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)["data"]
        assert data["total"] == 1
        assert data["chats"][0]["chat_name"] == "FreshGroup"


class TestListenPersist:
    def test_listen_persist_reconnects_until_stopped(self, runner, monkeypatch):
        import contextlib

        import tg_cli.cli.tg as tg_mod

        calls: list[str] = []
        sleeps: list[int] = []

        @contextlib.asynccontextmanager
        async def fake_connect():
            yield object()

        async def fake_listen(client, chats=None):
            calls.append("listen")
            return "disconnected" if len(calls) == 1 else "stopped"

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "listen", fake_listen)
        monkeypatch.setattr(tg_mod.time, "sleep", lambda seconds: sleeps.append(seconds))

        result = runner.invoke(cli, ["listen", "--persist", "--retry-seconds", "1"])
        assert result.exit_code == 0
        assert len(calls) == 2
        assert sleeps == [1]
        assert "Reconnecting in 1s" in result.output


class TestAmbiguousChat:
    def test_search_ambiguous_chat(self, runner, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        db = MessageDB(db_path=db_path)
        db.insert_message(
            chat_id=100,
            chat_name="Dev Group",
            msg_id=1,
            sender_id=1,
            sender_name="Alice",
            content="hello",
            timestamp=datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc),
        )
        db.insert_message(
            chat_id=200,
            chat_name="Dev Chat",
            msg_id=2,
            sender_id=2,
            sender_name="Bob",
            content="world",
            timestamp=datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc),
        )

        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["search", "hello", "--chat", "Dev"])
        assert result.exit_code == 0
        assert "matches multiple local chats" in result.output
        assert "Dev Group" in result.output
        assert "Dev Chat" in result.output


class TestExport:
    def test_export_text(self, runner, populated_db, tmp_path, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        out_file = str(tmp_path / "export.txt")
        result = runner.invoke(cli, ["export", "TestGroup", "-o", out_file])
        assert result.exit_code == 0
        assert "Exported" in result.output

        content = Path(out_file).read_text()
        assert "Alice:" in content

    def test_export_json(self, runner, populated_db, tmp_path, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        out_file = str(tmp_path / "export.json")
        result = runner.invoke(cli, ["export", "TestGroup", "-f", "json", "-o", out_file])
        assert result.exit_code == 0

        data = json.loads(Path(out_file).read_text())
        assert isinstance(data, list)
        assert len(data) > 0

    def test_export_not_found(self, runner, populated_db, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        result = runner.invoke(cli, ["export", "NonexistentGroup"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_export_yaml(self, runner, populated_db, tmp_path, monkeypatch):
        db, db_path = populated_db
        import tg_cli.db as db_mod

        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
        out_file = str(tmp_path / "export.yaml")
        result = runner.invoke(cli, ["export", "TestGroup", "-f", "yaml", "-o", out_file])
        assert result.exit_code == 0

        data = yaml.safe_load(Path(out_file).read_text())
        assert isinstance(data, list)
        assert data[0]["chat_name"] == "TestGroup"


class TestHelp:
    def test_main_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "tg" in result.output

    def test_tg_help(self, runner):
        result = runner.invoke(cli, ["chats", "--help"])
        assert result.exit_code == 0
        assert "chats" in result.output.lower() or "telegram" in result.output.lower()

    def test_today_help(self, runner):
        result = runner.invoke(cli, ["today", "--help"])
        assert result.exit_code == 0
        assert "today" in result.output.lower() or "chat" in result.output.lower()


class TestSend:
    def test_send_basic(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMsg:
            id = 42

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                assert chat == "TestChat"
                assert message == "Hello!"
                assert reply_to is None
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "TestChat", "Hello!", "--confirm"])
        assert result.exit_code == 0
        assert "Message sent" in result.output
        assert "42" in result.output

    def test_send_with_reply(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMsg:
            id = 99

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                assert reply_to == 12345
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "TestChat", "Reply!", "--reply", "12345", "--confirm"])
        assert result.exit_code == 0

    def test_send_yaml(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMsg:
            id = 77

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "TestChat", "Hello!", "--confirm", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["ok"] is True
        assert data["data"]["sent"] is True
        assert data["data"]["msg_id"] == 77

    def test_send_yaml_with_reply(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMsg:
            id = 88

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "TestChat", "Hi!", "-r", "999", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["data"]["reply_to"] == 999

    def test_send_numeric_chat(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        class FakeMsg:
            id = 55

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                assert chat == 12345  # Should be parsed as int
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "12345", "Hello!"])
        assert result.exit_code == 0


class TestChats:
    def test_chats_rich(self, runner, monkeypatch):

        import tg_cli.cli.tg as tg_mod

        async def fake_list_chats(client, chat_type=None):
            return [
                {"id": 100, "name": "Alice", "type": "user", "unread": 3},
                {"id": 200, "name": "Dev Group", "type": "group", "unread": 0},
            ]

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "list_chats", fake_list_chats)
        result = runner.invoke(cli, ["chats"])
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "Dev Group" in result.output
        assert "Total: 2 chats" in result.output

    def test_chats_yaml(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        async def fake_list_chats(client, chat_type=None):
            return [{"id": 100, "name": "Alice", "type": "user", "unread": 0}]

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "list_chats", fake_list_chats)
        result = runner.invoke(cli, ["chats", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["ok"] is True
        assert data["data"][0]["name"] == "Alice"

    def test_chats_with_type_filter(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        async def fake_list_chats(client, chat_type=None):
            assert chat_type == "channel"
            return [{"id": 300, "name": "News", "type": "channel", "unread": 5}]

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "list_chats", fake_list_chats)
        result = runner.invoke(cli, ["chats", "--type", "channel"])
        assert result.exit_code == 0
        assert "News" in result.output


class TestHistory:
    def test_history_rich(self, runner, monkeypatch, tmp_path):
        import tg_cli.cli.tg as tg_mod
        import tg_cli.db as db_mod

        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))
        monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)

        async def fake_fetch_history(client, chat, limit=1000, db=None, on_progress=None):
            return {"stored": 42, "seen": 42, "status": "complete", "error": None}

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "fetch_history", fake_fetch_history)
        result = runner.invoke(cli, ["history", "TestChat"])
        assert result.exit_code == 0
        assert "42" in result.output


class TestInfo:
    def test_info_yaml(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        async def fake_get_chat_info(client, chat):
            return {"Title": "Dev Group", "ID": "100", "Type": "Group"}

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "get_chat_info", fake_get_chat_info)
        result = runner.invoke(cli, ["info", "TestChat", "--yaml"])
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert data["ok"] is True
        assert data["data"]["Title"] == "Dev Group"

    def test_info_not_found(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        async def fake_get_chat_info(client, chat):
            return None

        @asynccontextmanager
        async def fake_connect():
            yield object()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        monkeypatch.setattr(tg_mod, "get_chat_info", fake_get_chat_info)
        result = runner.invoke(cli, ["info", "Missing"])
        assert result.exit_code == 0
        assert "Could not find chat" in result.output


class TestSendDryRun:
    def test_default_is_dry_run(self, runner):
        """Without --confirm nothing connects and nothing is sent."""
        result = runner.invoke(cli, ["send", "TestChat", "Hello!"])
        assert result.exit_code == 0
        assert "DRY-RUN" in result.output
        assert "Message sent" not in result.output

    def test_dry_run_yaml_payload(self, runner):
        result = runner.invoke(cli, ["send", "TestChat", "Hello!", "--yaml"])
        assert result.exit_code == 0
        assert "dry_run: true" in result.output
        assert "sent: false" in result.output

    def test_confirmed_send_is_logged(self, runner, monkeypatch, tmp_path):
        import tg_cli.cli.tg as tg_mod

        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        class FakeMsg:
            id = 7

        class FakeClient:
            async def send_message(self, chat, message, reply_to=None, **kwargs):
                return FakeMsg()

        @asynccontextmanager
        async def fake_connect():
            yield FakeClient()

        monkeypatch.setattr(tg_mod, "connect", fake_connect)
        result = runner.invoke(cli, ["send", "TestChat", "Привет, лог!", "--confirm"])
        assert result.exit_code == 0
        log_text = (tmp_path / "sent.log").read_text()
        assert "TestChat" in log_text
        assert "Привет, лог!" in log_text


class TestFormatConflictPreflight:
    def test_conflict_rejected_before_send(self, runner, monkeypatch):
        """--json --yaml must fail BEFORE any Telegram mutation (#27)."""
        import tg_cli.cli.tg as tg_mod

        calls = []

        @asynccontextmanager
        async def spy_connect():
            calls.append("connect")
            yield object()

        monkeypatch.setattr(tg_mod, "connect", spy_connect)
        result = runner.invoke(
            cli, ["send", "Chat", "hello", "--confirm", "--json", "--yaml"]
        )
        assert result.exit_code == 2
        assert "only one of" in result.output.lower()
        assert calls == []  # no connection, no send

    def test_conflict_rejected_before_sync(self, runner, monkeypatch):
        import tg_cli.cli.tg as tg_mod

        calls = []

        async def spy_sync(*a, **kw):
            calls.append("sync")
            return {}

        monkeypatch.setattr(tg_mod, "sync_all_dialogs", spy_sync)
        result = runner.invoke(cli, ["refresh", "--json", "--yaml"])
        assert result.exit_code == 2
        assert calls == []
