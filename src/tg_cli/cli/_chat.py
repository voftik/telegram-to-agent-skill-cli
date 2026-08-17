"""Shared chat resolution helpers for CLI commands."""

from rich.markup import escape
from rich.table import Table

from ..console import console
from ..db import MessageDB
from ._output import emit_error


def _parse_chat(chat: str) -> str | int:
    """Parse a chat argument: return int if numeric, else the original string."""
    try:
        return int(chat)
    except ValueError:
        return chat


def resolve_chat_id_or_print(
    db: MessageDB,
    chat: str | None,
    *,
    allow_missing: bool = False,
) -> int | None:
    """Resolve a user-supplied chat filter and print helpful errors."""
    if not chat:
        return None

    matches = db.find_chats(chat)
    if not matches:
        if allow_missing:
            return None
        if emit_error("chat_not_found", f"Chat '{chat}' not found in database."):
            raise SystemExit(1) from None
        console.print(f"[red]Chat '{escape(chat)}' not found in database.[/red]")
        # Same failure — same non-zero exit code in every output mode (#40).
        raise SystemExit(1)

    if len(matches) == 1:
        return matches[0]["chat_id"]

    table = Table(title=f"Ambiguous chat: {chat}")
    table.add_column("Chat ID", style="dim")
    table.add_column("Chat Name", style="bold")
    table.add_column("Messages", justify="right")
    for match in matches[:10]:
        table.add_row(
            str(match["chat_id"]),
            escape(match.get("chat_name") or "—"),
            str(match.get("msg_count") or 0),
        )

    if emit_error(
        "chat_ambiguous",
        f"Chat '{chat}' matches multiple local chats.",
        details={"query": chat, "matches": matches[:10]},
    ):
        raise SystemExit(1) from None
    console.print(f"[red]Chat '{escape(chat)}' matches multiple local chats.[/red]")
    console.print(table)
    console.print("[yellow]Use a more specific name or the numeric chat ID.[/yellow]")
    raise SystemExit(1)
