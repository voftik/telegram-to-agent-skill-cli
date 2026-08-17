"""Data commands — export, purge."""

import click

from ..console import console
from ..db import MessageDB
from ._chat import resolve_chat_id_or_print


@click.group("data")
def data_group():
    """Data management commands (registered at top-level)."""


@data_group.command("export")
@click.argument("chat")
@click.option("-f", "--format", "fmt", type=click.Choice(["text", "json", "yaml"]), default="text")
@click.option("-o", "--output", "output_file", help="Output file path")
@click.option("--hours", type=int, help="Only export last N hours")
def export(chat: str, fmt: str, output_file: str | None, hours: int | None):
    """Export ALL messages of CHAT to text, JSON, or YAML.

    The payload streams straight to stdout (or the file) without Rich
    wrapping/markup, so `tg export … > file` is always parseable (#35);
    diagnostics go to stderr. No silent row caps — the export paginates
    through the entire range.
    """
    import json as _json
    import sys as _sys

    import yaml as _yaml

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat_id is None:
            raise SystemExit(1)

        out = open(output_file, "w", encoding="utf-8") if output_file else _sys.stdout
        count = 0
        try:
            if fmt == "json":
                out.write("[\n")
            for msg in db.iter_messages(chat_id=chat_id, hours=hours):
                count += 1
                if fmt == "text":
                    ts = (msg.get("timestamp") or "")[:19]
                    sender = msg.get("sender_name") or "Unknown"
                    out.write(f"[{ts}] {sender}: {msg.get('content') or ''}\n")
                elif fmt == "json":
                    if count > 1:
                        out.write(",\n")
                    out.write(_json.dumps(msg, ensure_ascii=False, default=str))
                else:
                    out.write(_yaml.safe_dump([msg], allow_unicode=True, width=10**9))
            if fmt == "json":
                out.write("\n]\n")
        finally:
            if output_file:
                out.close()
            else:
                out.flush()

    if count == 0:
        console.print(f"[yellow]No messages found for '{chat}'.[/yellow]")
        raise SystemExit(1)
    # console targets stderr — safe alongside a stdout payload
    where = output_file or "stdout"
    console.print(f"[green]✓[/green] Exported {count} messages to {where}")


@data_group.command("purge")
@click.argument("chat")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation")
@click.option(
    "--keep-files",
    is_flag=True,
    help="Keep downloaded attachment files on disk (default: delete them)",
)
def purge(chat: str, yes: bool, keep_files: bool):
    """Delete ALL local data of CHAT: messages, search index, links,
    attachment metadata and (unless --keep-files) downloaded files."""
    from ..client import remove_local_files
    from ..config import get_data_dir

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat_id is None:
            return

        if not yes:
            count = db.count(chat_id)
            if not click.confirm(f"Delete {count} messages from chat {chat_id}?"):
                return

        res = db.delete_chat(chat_id)

    files_note = "kept"
    if not keep_files:
        remove_local_files(res["files"])
        chat_dir = get_data_dir() / "files" / str(chat_id)
        try:
            if chat_dir.is_dir() and not any(chat_dir.iterdir()):
                chat_dir.rmdir()
        except OSError:
            pass
        files_note = f"{len(res['files'])} removed"
    console.print(
        f"[green]✓[/green] Purged chat {chat_id}: {res['messages']} messages, "
        f"{res['attachments']} attachments, {res['links']} links, "
        f"files: {files_note}"
    )
