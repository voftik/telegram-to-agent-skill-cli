"""Read-only MCP server over the local SQLite index (stdio transport).

A thin bridge for desktop chat apps that cannot run a CLI (Claude
Desktop, Perplexity, ChatGPT desktop). Hand-rolled JSON-RPC 2.0 over
newline-delimited stdio: the MCP core needed here (initialize, tools)
is four methods, which does not justify the official SDK's dependency
tree under our pip-audit gate.

Hard invariants:
- stdout carries ONLY protocol lines; all logging goes to stderr;
- this module never imports cli.*, client, rich or telethon — it can
  never touch the Telethon session, prompt, or write to Telegram;
- reads open the existing database only; a missing DB file is a tool
  error, never an implicit creation.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

from .config import get_db_path
from .db import MessageDB
from .links import parse_tme_message_link
from .skillpkg import package_version

log = logging.getLogger(__name__)

# Newest first; initialize echoes the client's version when supported.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "tg-local"

# Mirrors the skill's hard rules: 7 days default, never >200 messages.
MAX_LIMIT = 200
DEFAULT_HOURS = 168

INSTRUCTIONS = (
    "Read-only access to the local index of the user's Telegram messages"
    " (synced by the tg CLI). Typical flow: tg_chats to find a chat,"
    " tg_brief before deep reading, then tg_search / tg_recent /"
    " tg_thread / tg_links. Data freshness depends on host-side"
    " `tg refresh`; check last_msg timestamps in tg_chats. This server"
    " can never send, edit, or delete anything."
)


class _ToolError(Exception):
    """Tool-level failure surfaced to the model as isError content."""


# ─────────────────────── helpers ───────────────────────


_SLIM_KEYS = (
    "chat_id",
    "chat_name",
    "msg_id",
    "sender_name",
    "content",
    "timestamp",
)


def _slim(row: dict) -> dict:
    """Drop raw_json/platform/ids the model does not need — one media-heavy
    chat would otherwise dump kilobytes of Telethon JSON per message."""
    out = {k: row.get(k) for k in _SLIM_KEYS}
    if row.get("reply_to_msg_id"):
        out["reply_to_msg_id"] = row["reply_to_msg_id"]
    if row.get("has_media"):
        out["has_media"] = True
    return out


def _open_db() -> MessageDB:
    path = get_db_path()
    if not path.exists():
        raise _ToolError(
            f"Local Telegram index not found at {path}. On the host, run"
            " `tg setup` once, then `tg refresh` to sync. The MCP bridge"
            " is read-only and never syncs by itself."
        )
    return MessageDB(db_path=path)


def _require_nonempty(db: MessageDB) -> None:
    if db.count() == 0:
        raise _ToolError(
            "The local Telegram index is empty. On the host, run"
            " `tg refresh` (or `tg bootstrap start` for the full initial"
            " sync), then retry."
        )


def _resolve_chat(db: MessageDB, chat: str) -> int:
    matches = db.find_chats(str(chat))
    if len(matches) == 1:
        return matches[0]["chat_id"]
    if not matches:
        raise _ToolError(
            f"Chat '{chat}' not found in the local index."
            " Use tg_chats to list synced chats."
        )
    lines = [
        f"  chat_id={m['chat_id']}  {m['chat_name']}  ({m['msg_count']} messages)"
        for m in matches[:10]
    ]
    raise _ToolError(
        f"Chat '{chat}' is ambiguous, retry with the numeric chat_id:\n"
        + "\n".join(lines)
    )


def _clamp(value: Any, default: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


# ─────────────────────── tool handlers ───────────────────────


def _tool_search(args: dict) -> dict:
    query = args.get("query")
    if not query or not isinstance(query, str):
        raise _ToolError("'query' is required and must be a string.")
    if args.get("regex"):
        # Python re has no backtracking bound: a hostile pattern over
        # hostile stored content could wedge this single-threaded server.
        raise _ToolError(
            "Regex search is not available over the bridge. Use FTS syntax:"
            ' word* prefixes, "exact phrases", OR.'
        )
    limit = _clamp(args.get("limit"), 50, MAX_LIMIT)
    hours = _clamp(args.get("hours"), DEFAULT_HOURS, 24 * 365 * 20)
    with _open_db() as db:
        _require_nonempty(db)
        chat_id = _resolve_chat(db, args["chat"]) if args.get("chat") else None
        sender = args.get("sender") or None
        rows = db.search(query, chat_id=chat_id, sender=sender, hours=hours, limit=limit)
        return {
            "count": len(rows),
            "messages": [_slim(r) for r in rows],
            "truncated": len(rows) >= limit,
        }


def _tool_recent(args: dict) -> dict:
    limit = _clamp(args.get("limit"), 50, MAX_LIMIT)
    hours = _clamp(args.get("hours"), 24, 24 * 365 * 20)
    with _open_db() as db:
        _require_nonempty(db)
        chat_id = _resolve_chat(db, args["chat"]) if args.get("chat") else None
        rows = db.get_recent(
            chat_id=chat_id, sender=args.get("sender") or None, hours=hours, limit=limit
        )
        return {
            "count": len(rows),
            "messages": [_slim(r) for r in rows],
            "truncated": len(rows) >= limit,
        }


def _tool_chats(args: dict) -> dict:
    limit = _clamp(args.get("limit"), 50, 500)
    with _open_db() as db:
        _require_nonempty(db)
        query = args.get("query")
        chats = db.find_chats(str(query)) if query else db.get_chats()
        return {
            "count": len(chats[:limit]),
            "chats": chats[:limit],
            "total_messages": db.count(),
            "latest_message": db.get_latest_timestamp(),
        }


def _tool_brief(args: dict) -> dict:
    chat = args.get("chat")
    if not chat:
        raise _ToolError("'chat' is required.")
    with _open_db() as db:
        _require_nonempty(db)
        chat_id = _resolve_chat(db, chat)
        info = db.brief(chat_id)
        matches = db.find_chats(str(chat))
        name = matches[0]["chat_name"] if matches else str(chat)
        return {"chat_id": chat_id, "chat_name": name, **info}


def _tool_thread(args: dict) -> dict:
    url = args.get("url")
    chat = args.get("chat")
    msg_id = args.get("msg_id")
    with _open_db() as db:
        _require_nonempty(db)
        if url:
            parsed = parse_tme_message_link(str(url))
            if parsed is None:
                raise _ToolError(f"'{url}' is not a t.me message link.")
            chat_ref, msg_id = parsed
            chat_id = (
                chat_ref if isinstance(chat_ref, int) else _resolve_chat(db, chat_ref)
            )
        elif chat and msg_id is not None:
            chat_id = _resolve_chat(db, chat)
            try:
                msg_id = int(msg_id)
            except (TypeError, ValueError):
                raise _ToolError("'msg_id' must be an integer.") from None
        else:
            raise _ToolError("Pass either 'url' or both 'chat' and 'msg_id'.")
        rows = db.get_thread(chat_id, msg_id)
        if not rows:
            raise _ToolError(
                f"No thread found around msg_id={msg_id} in chat_id={chat_id}."
                " The message may be outside the synced range."
            )
        return {"count": len(rows), "messages": [_slim(r) for r in rows]}


def _tool_links(args: dict) -> dict:
    limit = _clamp(args.get("limit"), 50, 100)
    hours = _clamp(args.get("hours"), DEFAULT_HOURS, 24 * 365 * 20)
    kind = args.get("kind") or None
    with _open_db() as db:
        _require_nonempty(db)
        chat_id = _resolve_chat(db, args["chat"]) if args.get("chat") else None
        rows = db.get_links(chat_id=chat_id, hours=hours, kind=kind, limit=limit)
        return {"count": len(rows), "links": rows}


# ─────────────────────── tool registry ───────────────────────


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_CHAT_PROP = {"type": "string", "description": "Chat name or numeric chat_id"}

TOOLS: dict[str, tuple[dict, Callable[[dict], dict]]] = {
    "tg_search": (
        {
            "name": "tg_search",
            "description": (
                "Full-text search (FTS5: word* prefixes, phrases, OR) over"
                " locally synced Telegram messages."
            ),
            "inputSchema": _schema(
                {
                    "query": {
                        "type": "string",
                        "description": 'Search query; supports prefix* and "phrase"',
                    },
                    "chat": _CHAT_PROP,
                    "sender": {"type": "string"},
                    "hours": {"type": "integer", "default": DEFAULT_HOURS},
                    "limit": {"type": "integer", "default": 50, "maximum": MAX_LIMIT},
                },
                required=["query"],
            ),
        },
        _tool_search,
    ),
    "tg_recent": (
        {
            "name": "tg_recent",
            "description": "Recent messages from one chat or all chats, chronological.",
            "inputSchema": _schema(
                {
                    "chat": _CHAT_PROP,
                    "sender": {"type": "string"},
                    "hours": {"type": "integer", "default": 24},
                    "limit": {"type": "integer", "default": 50, "maximum": MAX_LIMIT},
                }
            ),
        },
        _tool_recent,
    ),
    "tg_chats": (
        {
            "name": "tg_chats",
            "description": (
                "List locally synced chats with message counts and last-message"
                " time (freshness); optionally filter by name."
            ),
            "inputSchema": _schema(
                {
                    "query": {"type": "string", "description": "Name filter"},
                    "limit": {"type": "integer", "default": 50},
                }
            ),
        },
        _tool_chats,
    ),
    "tg_brief": (
        {
            "name": "tg_brief",
            "description": (
                "Chat passport: volume, 7d/30d activity, peak days, top senders,"
                " attachment and link counts. Call before deep-reading a chat."
            ),
            "inputSchema": _schema({"chat": _CHAT_PROP}, required=["chat"]),
        },
        _tool_brief,
    ),
    "tg_thread": (
        {
            "name": "tg_thread",
            "description": (
                "Reconstruct the full reply thread around one message"
                " (by chat + msg_id, or by a t.me link)."
            ),
            "inputSchema": _schema(
                {
                    "chat": _CHAT_PROP,
                    "msg_id": {"type": "integer"},
                    "url": {"type": "string", "description": "t.me message link"},
                }
            ),
        },
        _tool_thread,
    ),
    "tg_links": (
        {
            "name": "tg_links",
            "description": (
                "Links shared in chats, with agent-fetchable fetch_url"
                " (Google Docs resolve to export endpoints)."
            ),
            "inputSchema": _schema(
                {
                    "chat": _CHAT_PROP,
                    "hours": {"type": "integer", "default": DEFAULT_HOURS},
                    "kind": {
                        "type": "string",
                        "enum": ["gdoc", "gsheet", "gslides", "tme", "web"],
                    },
                    "limit": {"type": "integer", "default": 50, "maximum": 100},
                }
            ),
        },
        _tool_links,
    ),
}


# ─────────────────────── protocol ───────────────────────


def _rpc_result(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_text(payload: Any, is_error: bool = False) -> dict:
    text = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    result: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _handle_initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": package_version()},
        "instructions": INSTRUCTIONS,
    }


def _handle_tools_call(params: dict) -> dict:
    name = params.get("name")
    if name not in TOOLS:
        raise KeyError(name)
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _tool_text("'arguments' must be an object.", is_error=True)
    handler = TOOLS[name][1]
    try:
        return _tool_text(handler(args))
    except _ToolError as e:
        return _tool_text(str(e), is_error=True)
    except Exception as e:  # noqa: BLE001 - a tool bug must not kill the server
        log.exception("tool %s failed", name)
        return _tool_text(f"Internal error in {name}: {type(e).__name__}", is_error=True)


def _dispatch(obj: dict) -> dict | None:
    method = obj.get("method")
    id_ = obj.get("id")
    is_notification = "id" not in obj

    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if not isinstance(method, str):
        return None if is_notification else _rpc_error(id_, -32600, "Invalid Request")
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        return (
            None
            if is_notification
            else _rpc_error(id_, -32602, "params must be an object")
        )

    if method == "initialize":
        result = _handle_initialize(params)
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [spec for spec, _ in TOOLS.values()]}
    elif method == "tools/call":
        try:
            result = _handle_tools_call(params)
        except KeyError as e:
            return (
                None
                if is_notification
                else _rpc_error(id_, -32602, f"Unknown tool: {e.args[0]}")
            )
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        return None if is_notification else _rpc_error(id_, -32601, f"Method not found: {method}")

    return None if is_notification else _rpc_result(id_, result)


def handle_message(line: str) -> str | None:
    """Process one JSON-RPC line; return a serialized response or None."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return _serialize(_rpc_error(None, -32700, "Parse error"))
    if isinstance(obj, list):
        # JSON-RPC batch (required by the 2025-03-26 revision).
        if not obj:
            return _serialize(_rpc_error(None, -32600, "Invalid Request"))
        responses = [r for r in (_safe_dispatch(item) for item in obj) if r is not None]
        if not responses:
            return None  # all notifications
        return json.dumps(responses, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(obj, dict):
        return _serialize(_rpc_error(None, -32600, "Invalid Request"))
    resp = _safe_dispatch(obj)
    return _serialize(resp) if resp is not None else None


def _safe_dispatch(obj) -> dict | None:
    if not isinstance(obj, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    try:
        return _dispatch(obj)
    except Exception as e:  # noqa: BLE001 - the loop must survive anything
        log.exception("dispatch failed")
        if "id" not in obj:
            return None  # never answer a notification, even a broken one
        return _rpc_error(obj.get("id"), -32603, f"Internal error: {type(e).__name__}")


def _serialize(resp: dict) -> str:
    # Compact separators guarantee a single physical line per response.
    return json.dumps(resp, ensure_ascii=False, separators=(",", ":"))


def serve(stdin=None, stdout=None) -> int:
    """Blocking stdio loop. EOF on stdin means the host is done: exit 0."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    # MCP mandates UTF-8 on the wire; Windows pipes default to the ANSI
    # code page, which would mangle Cyrillic content in both directions.
    if stdin is None and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if stdout is None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    try:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            resp = handle_message(line)
            if resp is not None:
                out.write(resp + "\n")
                out.flush()
    except KeyboardInterrupt:
        pass
    return 0
