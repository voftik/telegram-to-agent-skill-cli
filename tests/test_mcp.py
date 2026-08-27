"""Tests for the read-only MCP bridge (tg mcp) — in-process, no network."""

from __future__ import annotations

import io
import json

import pytest
from conftest import make_msg

from tg_cli import mcpserver
from tg_cli.mcpserver import TOOLS, handle_message, serve


def rpc(method: str, params: dict | None = None, id_: int | str | None = 1) -> str:
    obj: dict = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        obj["id"] = id_
    if params is not None:
        obj["params"] = params
    return json.dumps(obj)


def call(name: str, arguments: dict | None = None) -> dict:
    """tools/call helper: returns the parsed result object."""
    raw = handle_message(rpc("tools/call", {"name": name, "arguments": arguments or {}}))
    assert raw is not None
    resp = json.loads(raw)
    assert "error" not in resp, resp
    return resp["result"]


def payload_of(result: dict) -> dict:
    assert result.get("isError") is not True, result
    return json.loads(result["content"][0]["text"])


def error_text_of(result: dict) -> str:
    assert result.get("isError") is True, result
    return result["content"][0]["text"]


class TestHandshake:
    def test_initialize_echoes_supported_version(self):
        raw = handle_message(rpc("initialize", {"protocolVersion": "2024-11-05"}))
        result = json.loads(raw)["result"]
        assert result["protocolVersion"] == "2024-11-05"

    def test_initialize_unknown_version_offers_newest(self):
        raw = handle_message(rpc("initialize", {"protocolVersion": "1999-01-01"}))
        result = json.loads(raw)["result"]
        assert result["protocolVersion"] == mcpserver.PROTOCOL_VERSIONS[0]

    def test_initialize_shape(self):
        result = json.loads(handle_message(rpc("initialize", {})))["result"]
        assert result["serverInfo"]["name"] == "tg-local"
        assert result["serverInfo"]["version"]
        assert "tools" in result["capabilities"]
        assert "read-only" in result["instructions"].lower() or "never send" in (
            result["instructions"].lower()
        )

    def test_initialized_notification_is_silent(self):
        assert handle_message(rpc("notifications/initialized", id_=None)) is None


class TestDiscovery:
    def test_tools_list(self):
        result = json.loads(handle_message(rpc("tools/list")))["result"]
        tools = result["tools"]
        assert len(tools) == 6
        for tool in tools:
            assert tool["name"].startswith("tg_")
            assert tool["description"]
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            for prop in schema["properties"].values():
                assert "type" in prop

    def test_ping(self):
        assert json.loads(handle_message(rpc("ping")))["result"] == {}

    def test_resources_and_prompts_empty(self):
        assert json.loads(handle_message(rpc("resources/list")))["result"] == {
            "resources": []
        }
        assert json.loads(handle_message(rpc("prompts/list")))["result"] == {
            "prompts": []
        }


class TestRobustness:
    def test_unknown_method(self):
        resp = json.loads(handle_message(rpc("frobnicate/all")))
        assert resp["error"]["code"] == -32601

    def test_unknown_notification_is_silent(self):
        assert handle_message(rpc("frobnicate/all", id_=None)) is None

    def test_malformed_json_then_loop_survives(self):
        resp = json.loads(handle_message("{not json"))
        assert resp["error"]["code"] == -32700
        assert resp["id"] is None
        # The same process still answers the next request.
        assert json.loads(handle_message(rpc("ping")))["result"] == {}

    def test_batch_rejected(self):
        resp = json.loads(handle_message("[]"))
        assert resp["error"]["code"] == -32600

    def test_unknown_tool(self):
        raw = handle_message(rpc("tools/call", {"name": "tg_nuke", "arguments": {}}))
        assert json.loads(raw)["error"]["code"] == -32602


class TestTools:
    def test_search(self, populated_db):
        payload = payload_of(call("tg_search", {"query": "Python"}))
        assert payload["count"] >= 1
        msg = payload["messages"][0]
        assert "raw_json" not in msg
        assert "platform" not in msg
        assert "Python" in msg["content"]

    def test_search_regex(self, populated_db):
        payload = payload_of(
            call("tg_search", {"query": r"Message \d+", "regex": True, "limit": 5})
        )
        assert payload["count"] == 5

    def test_recent(self, populated_db):
        payload = payload_of(call("tg_recent", {"chat": "TestGroup", "hours": 48}))
        assert payload["count"] >= 1
        # Chronological order.
        ts = [m["timestamp"] for m in payload["messages"]]
        assert ts == sorted(ts)

    def test_chats(self, populated_db):
        payload = payload_of(call("tg_chats"))
        assert payload["total_messages"] == 10
        assert payload["chats"][0]["chat_name"] == "TestGroup"
        assert payload["latest_message"]

    def test_brief(self, populated_db):
        payload = payload_of(call("tg_brief", {"chat": "TestGroup"}))
        assert payload["chat_id"] == 100
        assert payload["chat_name"] == "TestGroup"
        assert payload["total"] == 10
        assert payload["top_senders"]

    def test_thread(self, populated_db):
        db, _path = populated_db
        db.insert_batch(
            [
                make_msg(chat_id=100, chat_name="TestGroup", msg_id=50, content="root"),
                make_msg(
                    chat_id=100, chat_name="TestGroup", msg_id=51,
                    content="reply", reply_to_msg_id=50,
                ),
            ]
        )
        payload = payload_of(call("tg_thread", {"chat": "TestGroup", "msg_id": 51}))
        assert payload["count"] == 2
        contents = {m["content"] for m in payload["messages"]}
        assert contents == {"root", "reply"}

    def test_links(self, populated_db):
        db, _path = populated_db
        db.insert_links(
            [
                {
                    "chat_id": 100,
                    "msg_id": 1,
                    "url": "https://docs.google.com/document/d/abc/edit",
                    "fetch_url": "https://docs.google.com/document/d/abc/export?format=txt",
                    "kind": "gdoc",
                }
            ]
        )
        payload = payload_of(call("tg_links", {"kind": "gdoc"}))
        assert payload["count"] == 1
        assert payload["links"][0]["fetch_url"].endswith("format=txt")


class TestToolErrors:
    def test_chat_not_found(self, populated_db):
        text = error_text_of(call("tg_brief", {"chat": "NoSuchChat"}))
        assert "tg_chats" in text

    def test_ambiguous_chat(self, populated_db):
        db, _path = populated_db
        db.insert_batch(
            [make_msg(chat_id=200, chat_name="TestGroup Two", msg_id=1, content="x")]
        )
        text = error_text_of(call("tg_brief", {"chat": "Group"}))
        assert "chat_id=" in text

    def test_missing_db_is_not_created(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope" / "messages.db"
        monkeypatch.setenv("DB_PATH", str(missing))
        text = error_text_of(call("tg_search", {"query": "x"}))
        assert "tg setup" in text
        assert not missing.exists()

    def test_empty_db_hints_refresh(self, monkeypatch, tmp_path):
        from tg_cli.db import MessageDB

        db_path = tmp_path / "empty.db"
        MessageDB(db_path=db_path).close()
        monkeypatch.setenv("DB_PATH", str(db_path))
        text = error_text_of(call("tg_search", {"query": "x"}))
        assert "tg refresh" in text

    def test_limit_clamped(self, populated_db):
        payload = payload_of(
            call("tg_recent", {"chat": "TestGroup", "hours": 999, "limit": 10000})
        )
        assert payload["count"] <= mcpserver.MAX_LIMIT

    def test_search_requires_query(self, populated_db):
        text = error_text_of(call("tg_search", {}))
        assert "query" in text

    def test_thread_requires_target(self, populated_db):
        text = error_text_of(call("tg_thread", {}))
        assert "msg_id" in text


class TestServeLoop:
    def test_two_requests_then_eof(self):
        stdin = io.StringIO(rpc("initialize", {}) + "\n" + rpc("ping", id_=2) + "\n")
        stdout = io.StringIO()
        assert serve(stdin=stdin, stdout=stdout) == 0
        lines = [line for line in stdout.getvalue().splitlines() if line]
        assert len(lines) == 2
        first, second = (json.loads(line) for line in lines)
        assert first["id"] == 1 and "protocolVersion" in first["result"]
        assert second["id"] == 2 and second["result"] == {}

    def test_stdout_carries_only_protocol_lines(self, populated_db):
        stdin = io.StringIO(
            "\n".join(
                [
                    rpc("initialize", {}),
                    rpc("notifications/initialized", id_=None),
                    rpc("tools/call", {"name": "tg_chats", "arguments": {}}, id_=2),
                ]
            )
            + "\n"
        )
        stdout = io.StringIO()
        serve(stdin=stdin, stdout=stdout)
        lines = stdout.getvalue().splitlines()
        assert len(lines) == 2  # notification produced no output
        for line in lines:
            json.loads(line)  # every stdout line is valid JSON


class TestToolCount:
    def test_registry_matches_spec_names(self):
        assert set(TOOLS) == {
            "tg_search", "tg_recent", "tg_chats", "tg_brief", "tg_thread", "tg_links",
        }
        for name, (spec, handler) in TOOLS.items():
            assert spec["name"] == name
            assert callable(handler)


@pytest.mark.parametrize("args", [["mcp", "--help"]])
def test_cli_help(args):
    from click.testing import CliRunner

    from tg_cli.cli.main import cli

    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    assert "read-only" in result.output
