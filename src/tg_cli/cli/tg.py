"""Telegram subcommands — send, edit, delete, and more."""

import asyncio
import time

import click
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..client import connect, fetch_history, get_chat_info, list_chats, listen
from ..console import console
from ..db import MessageDB
from ._chat import _parse_chat, resolve_chat_id_or_print
from ._output import (
    default_structured_format,
    dump_structured,
    emit_structured,
    error_payload,
    structured_output_options,
    success_payload,
)
from ._sync import sync_all_dialogs, sync_chat_dialog


def _telegram_user_payload(me) -> dict[str, str | int]:
    """Normalize Telegram user info for structured agent output."""
    name = " ".join(part for part in [me.first_name, me.last_name] if part).strip()
    return {
        "id": me.id,
        "name": name,
        "username": me.username or "",
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "phone": me.phone or "",
    }


def _run_async(coro):
    """asyncio.run with human-friendly failure for busy/broken sessions.

    A locked Telethon session (another sync holds it) or a network error
    must produce a clear one-liner and exit code 1, not a traceback.
    """
    from ._output import emit_error

    try:
        return asyncio.run(coro)
    except SystemExit:
        raise
    except Exception as e:
        message = str(e) or type(e).__name__
        if "database is locked" in message:
            message = (
                "Telegram session is busy (another sync/download holds it). "
                "Retry after it finishes."
            )
        if emit_error("telegram_error", message):
            raise SystemExit(1) from None
        console.print(f"[red]✗ {message}[/red]")
        raise SystemExit(1) from None


@click.group("tg")
def tg_group():
    """Telegram operations — connect, fetch, sync, listen."""
    pass


@tg_group.command("chats")
@click.option("--type", "chat_type", help="Filter by type: user, group, supergroup, channel")
@structured_output_options
def tg_chats(chat_type: str | None, as_json: bool, as_yaml: bool):
    """List joined Telegram chats."""

    async def _run():
        async with connect() as client:
            return await list_chats(client, chat_type)

    chats = _run_async(_run())
    if emit_structured(chats, as_json=as_json, as_yaml=as_yaml):
        return

    table = Table(title="Telegram Chats")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type", style="cyan")
    table.add_column("Unread", justify="right")

    for c in chats:
        table.add_row(str(c["id"]), c["name"], c["type"], str(c["unread"]))

    console.print(table)
    console.print(f"\nTotal: {len(chats)} chats")


@tg_group.command("history")
@click.argument("chat")
@click.option("-n", "--limit", default=1000, help="Max messages to fetch")
@structured_output_options
def tg_history(chat: str, limit: int, as_json: bool, as_yaml: bool):
    """Fetch historical messages from CHAT (name, username, or numeric ID)."""

    async def _run():
        with MessageDB() as db:
            async with connect() as client:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task(f"Fetching messages from {chat}...", total=None)

                    def on_progress(count: int):
                        progress.update(task, description=f"Stored {count} messages...")

                    result = await fetch_history(
                        client, _parse_chat(chat), limit=limit, db=db, on_progress=on_progress
                    )
                return result

    res = _run_async(_run())
    payload = {"stored": res["stored"], "status": res["status"], "chat": chat}
    if res["error"]:
        payload["error"] = res["error"]
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        if res["status"] == "failed":
            raise SystemExit(1)
        return
    console.print(f"\n[green]\u2713[/green] Stored {res['stored']} messages from {chat}")
    if res["status"] != "complete":
        detail = res["error"] or "gap recorded"
        console.print(f"[yellow]status: {res['status']} \u2014 {detail}[/yellow]")
        if res["status"] == "failed":
            raise SystemExit(1)


@tg_group.command("sync")
@click.argument("chat")
@click.option("-n", "--limit", default=5000, help="Max messages per sync")
@structured_output_options
def tg_sync(chat: str, limit: int, as_json: bool, as_yaml: bool):
    """Incremental sync — fetch only new messages from CHAT."""

    async def _run():
        with MessageDB() as db:
            # Resolve chat_id to get last_msg_id
            chat_id = resolve_chat_id_or_print(db, chat, allow_missing=True)
            matches = db.find_chats(chat)
            if len(matches) > 1:
                resolve_chat_id_or_print(db, chat)
                return None
            last_id = db.get_last_msg_id(chat_id) if chat_id else 0
        if last_id:
            console.print(f"Syncing from msg_id > {last_id}...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task_id = progress.add_task(f"Syncing {chat}...", total=None)

            def on_progress(count: int):
                progress.update(task_id, description=f"Stored {count} new messages...")

            return await sync_chat_dialog(chat, limit=limit, on_progress=on_progress)

    res = _run_async(_run())
    if res is None:
        return
    payload = {"synced": res["stored"], "status": res["status"], "chat": chat}
    if res["error"]:
        payload["error"] = res["error"]
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        if res["status"] == "failed":
            raise SystemExit(1)
        return
    console.print(f"\n[green]\u2713[/green] Synced {res['stored']} new messages from {chat}")
    if res["status"] == "failed":
        console.print(f"[red]sync failed: {res['error']}[/red]")
        raise SystemExit(1)


@tg_group.command("sync-all")
@click.option("-n", "--limit", default=5000, help="Max messages per chat")
@click.option(
    "--delay",
    default=1.0,
    show_default=True,
    help="Seconds between chat syncs (anti-ban). Set 0 to disable.",
)
@click.option(
    "--max-chats",
    default=None,
    type=int,
    help="Max number of chats to sync per run (default: all)",
)
@structured_output_options
def tg_sync_all(limit: int, delay: float, max_chats: int | None, as_json: bool, as_yaml: bool):
    """Sync all currently available Telegram dialogs with a single connection."""

    async def _run():
        on_chat_done = None
        if not as_json and not as_yaml:
            console.print("Syncing all available chats...")

            def _on_chat_done(name: str, new_count: int, total: int):
                if new_count > 0:
                    console.print(f"  [green]✓[/green] {name}: +{new_count} (total: {total})")
                else:
                    console.print(f"  [dim]✓ {name}: no new messages[/dim]")

            on_chat_done = _on_chat_done

        return await sync_all_dialogs(
            limit=limit, on_chat_done=on_chat_done, delay=delay, max_chats=max_chats
        )

    report = _run_async(_run())
    _finish_sync_report(report, as_json=as_json, as_yaml=as_yaml)


def _finish_sync_report(report: dict, *, as_json: bool, as_yaml: bool) -> None:
    """Emit a sync_all pass report and exit non-zero on failures (#19)."""
    ok_pass = report["enumerated"] and report["failed"] == 0
    failed_chats = {
        cid: r for cid, r in report["results"].items() if r["status"] == "failed"
    }
    if emit_structured(report, as_json=as_json, as_yaml=as_yaml):
        if not ok_pass:
            raise SystemExit(1)
        return

    if not report["enumerated"]:
        console.print(f"[red]✗ {report['error']}[/red]")
        raise SystemExit(1)
    console.print(
        f"\n[green]✓[/green] Synced {report['new_messages']} new messages across "
        f"{report['total']} chats (ok: {report['ok']}, partial: {report['partial']},"
        f" failed: {report['failed']})"
    )
    for r in list(failed_chats.values())[:10]:
        console.print(f"  [red]✗ {r['name']}: {r['error']}[/red]")
    if not ok_pass:
        raise SystemExit(1)


@tg_group.command("refresh")
@click.option("-n", "--limit", default=5000, help="Max messages per chat")
@click.option(
    "--delay",
    default=1.0,
    show_default=True,
    help="Seconds between chat syncs (anti-ban). Set 0 to disable.",
)
@click.option(
    "--max-chats",
    default=None,
    type=int,
    help="Max number of chats to sync per run (default: all)",
)
@structured_output_options
def tg_refresh(limit: int, delay: float, max_chats: int | None, as_json: bool, as_yaml: bool):
    """Refresh the local cache from all current Telegram dialogs."""

    async def _run():
        on_chat_done = None
        if not as_json and not as_yaml:
            console.print("Refreshing local cache...")

            def _on_chat_done(name: str, new_count: int, total: int):
                if new_count > 0:
                    console.print(f"  [green]✓[/green] {name}: +{new_count} (total: {total})")
                else:
                    console.print(f"  [dim]✓ {name}: no new messages[/dim]")

            on_chat_done = _on_chat_done

        return await sync_all_dialogs(
            limit=limit, on_chat_done=on_chat_done, delay=delay, max_chats=max_chats
        )

    report = _run_async(_run())
    _finish_sync_report(report, as_json=as_json, as_yaml=as_yaml)


@tg_group.command("backfill")
@click.argument("chat")
@click.option("-n", "--limit", default=2000, show_default=True, help="Messages per run")
@structured_output_options
def tg_backfill(chat: str, limit: int, as_json: bool, as_yaml: bool):
    """Pull history older than the first sync captured (consumes backfill cursors).

    The first sync of a big chat stores its newest messages and records a
    backfill cursor. Run this (repeatedly, if needed) to walk the history
    all the way back without ever creating gaps.
    """
    from ._sync import backfill_chat_dialog

    res = _run_async(backfill_chat_dialog(chat, limit=limit))
    if emit_structured(res, as_json=as_json, as_yaml=as_yaml):
        if res["error"]:
            raise SystemExit(1)
        return
    if res["error"]:
        console.print(f"[red]✗ backfill failed: {res['error']}[/red]")
        raise SystemExit(1)
    if res["closed"] and not res["remaining"]:
        console.print(f"[green]✓[/green] History complete: +{res['stored']} messages")
    elif res["remaining"]:
        console.print(
            f"[green]✓[/green] +{res['stored']} messages, older history remains —"
            " run again to continue"
        )
    else:
        console.print("[dim]Nothing to backfill for this chat.[/dim]")


@tg_group.command("listen")
@click.argument("chats", nargs=-1)
@click.option("--persist", is_flag=True, help="Reconnect automatically if the connection drops")
@click.option(
    "--retry-seconds",
    default=5,
    show_default=True,
    help="Reconnect delay when using --persist",
)
def tg_listen(chats: tuple[str, ...], persist: bool, retry_seconds: int):
    """Real-time listener for new messages. Optionally specify CHATS to filter."""
    parsed: list[str | int] | None = None
    if chats:
        parsed = []
        for c in chats:
            try:
                parsed.append(int(c))
            except ValueError:
                parsed.append(c)

    async def _run_once():
        async with connect() as client:
            return await listen(client, chats=parsed)

    while True:
        try:
            result = asyncio.run(_run_once())
        except click.ClickException:
            raise
        except Exception as exc:
            if not persist:
                raise
            console.print(
                f"[yellow]Listener disconnected: {exc}. Retrying in {retry_seconds}s...[/yellow]"
            )
            time.sleep(retry_seconds)
            continue

        if not persist or result == "stopped":
            break

        console.print(
            f"[yellow]Listener disconnected. Reconnecting in {retry_seconds}s...[/yellow]"
        )
        time.sleep(retry_seconds)


@tg_group.command("info")
@click.argument("chat")
@structured_output_options
def tg_info(chat: str, as_json: bool, as_yaml: bool):
    """Show detailed info about CHAT."""

    async def _run():
        async with connect() as client:
            return await get_chat_info(client, _parse_chat(chat))

    info = _run_async(_run())
    if not info:
        console.print(f"[red]Could not find chat: {chat}[/red]")
        return

    if emit_structured(info, as_json=as_json, as_yaml=as_yaml):
        return

    table = Table(title="Chat Info", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")

    for k, v in info.items():
        table.add_row(k, v)

    console.print(table)


@tg_group.command("whoami")
@structured_output_options
def tg_whoami(as_json: bool, as_yaml: bool):
    """Show current logged-in user info."""

    async def _run():
        async with connect() as client:
            me = await client.get_me()
            return me

    fmt = default_structured_format(as_json=as_json, as_yaml=as_yaml)
    try:
        me = asyncio.run(_run())
    except Exception as exc:
        if fmt is not None:
            click.echo(dump_structured(error_payload("auth_error", str(exc)), fmt=fmt))
            raise SystemExit(1) from None
        raise click.ClickException(str(exc)) from exc

    info = _telegram_user_payload(me)

    if emit_structured(success_payload({"user": info}), as_json=as_json, as_yaml=as_yaml):
        return

    name = " ".join(p for p in [me.first_name, me.last_name] if p)
    table = Table(title=f"👤 {name}")
    table.add_column("Field", style="bold cyan")
    table.add_column("Value", style="green")
    table.add_row("ID", str(me.id))
    table.add_row("Name", name)
    if me.username:
        table.add_row("Username", f"@{me.username}")
    if me.phone:
        table.add_row("Phone", f"+{me.phone}")

    console.print(table)


@tg_group.command("status")
@structured_output_options
def tg_status(as_json: bool, as_yaml: bool):
    """Show Telegram authentication status (non-interactive, never prompts)."""
    from ..client import check_auth

    info = asyncio.run(check_auth())

    payload = {
        "authenticated": info["authenticated"],
        "reachable": info["reachable"],
    }
    if info["error"]:
        payload["error"] = info["error"]
    if info["authenticated"]:
        payload["user"] = {
            k: info[k] for k in ("id", "first_name", "last_name", "username", "phone")
        }
    if emit_structured(success_payload(payload), as_json=as_json, as_yaml=as_yaml):
        return

    if info["authenticated"]:
        name = " ".join(
            part for part in [info["first_name"], info["last_name"]] if part
        ).strip()
        console.print(f"[green]✓[/green] Authenticated as [bold]{name or info['id']}[/bold]")
        if info["username"]:
            console.print(f"[dim]@{info['username']}[/dim]")
    elif not info["reachable"]:
        console.print(f"[yellow]⚠ Network unreachable: {info['error']}[/yellow]")
    else:
        console.print("[yellow]Not authenticated. Run `tg whoami` to sign in.[/yellow]")


@tg_group.command("send")
@click.argument("chat")
@click.argument("message")
@click.option("-r", "--reply", type=int, default=None, help="Message ID to reply to")
@click.option("--no-preview", is_flag=True, help="Disable link preview")
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually send. Without this flag the command is a dry-run preview.",
)
@structured_output_options
def tg_send(
    chat: str,
    message: str,
    reply: int | None,
    no_preview: bool,
    confirm: bool,
    as_json: bool,
    as_yaml: bool,
):
    """Send a MESSAGE to CHAT (name, username, or numeric ID).

    Safety: without --confirm nothing is sent \u2014 the command prints a preview
    and exits. Agents must show the text to the user and get an explicit
    "yes" before re-running with --confirm. Every real send is logged to
    <data_dir>/sent.log.
    """
    if not confirm:
        payload = {
            "sent": False,
            "dry_run": True,
            "chat": chat,
            "message": message,
        }
        if reply is not None:
            payload["reply_to"] = reply
        if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]DRY-RUN \u2014 nothing was sent.[/yellow]")
        console.print(f"  chat: [cyan]{chat}[/cyan]")
        if reply is not None:
            console.print(f"  reply_to: {reply}")
        console.print(f"  text: {message}")
        console.print("[dim]Re-run with --confirm to actually send.[/dim]")
        return

    async def _run():
        async with connect() as client:
            msg = await client.send_message(
                _parse_chat(chat),
                message,
                reply_to=reply,
                link_preview=not no_preview,
            )
            return msg

    msg = _run_async(_run())
    _log_sent(chat, msg.id, message)
    payload = {"sent": True, "msg_id": msg.id, "chat": chat}
    if reply is not None:
        payload["reply_to"] = reply
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(f"[green]\u2713[/green] Message sent (id: {msg.id})")


def _log_sent(chat: str, msg_id: int, message: str) -> None:
    """Append an audit line for every real send."""
    from datetime import datetime, timezone

    from ..config import get_data_dir

    preview = message[:100].replace("\n", " ")
    line = f"{datetime.now(timezone.utc).isoformat()}\t{chat}\t{msg_id}\t{preview}\n"
    try:
        with open(get_data_dir() / "sent.log", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        console.print(f"[yellow]\u26a0 sent.log write failed: {e}[/yellow]")


@tg_group.group("bootstrap")
def tg_bootstrap():
    """Reboot-resilient initial sync (arms itself, then removes itself)."""


@tg_bootstrap.command("start")
@click.option("--delay", default=2.0, show_default=True, help="Seconds between chat syncs")
@click.option("-n", "--limit", default=5000, show_default=True, help="Max messages per chat")
def tg_bootstrap_start(delay: float, limit: int):
    """Arm the initial sync: starts now, auto-resumes after reboots,
    uninstalls itself after one full successful pass over all dialogs."""
    from .. import bootstrap as bs

    bs.write_marker(delay, limit)
    try:
        where = bs.install_autostart()
    except Exception as e:
        bs.clear_marker()
        console.print(f"[red]Autostart install failed: {e}[/red]")
        raise SystemExit(1) from None
    console.print(f"[green]✓[/green] Bootstrap armed: {where}")
    console.print(
        "[dim]Progress: tg bootstrap status · log: "
        f"{bs.get_data_dir() / 'bootstrap.log'}[/dim]"
    )


@tg_bootstrap.command("run", hidden=True)
def tg_bootstrap_run():
    """Worker invoked by launchd/systemd. Not meant for humans."""
    from .. import bootstrap as bs
    from ..client import connect, sync_all
    from ..db import MessageDB

    state = bs.read_marker()
    if state is None:
        # Nothing pending — clean exit stops KeepAlive relaunches; make sure
        # no stale autostart entry survives.
        bs.uninstall_autostart()
        return

    delay = float(state.get("delay", 2.0))
    limit = int(state.get("limit", 5000))

    async def _pass():
        async with connect() as client:
            with MessageDB() as db:
                report = await sync_all(client, db, limit_per_chat=limit, delay=delay)
                gaps_left = db.count_gaps(kind="gap")
                return report, gaps_left

    try:
        report, gaps_left = asyncio.run(_pass())
    except Exception as e:
        console.print(f"bootstrap pass failed, will retry: {e}")
        raise SystemExit(1) from None

    # A pass only counts when it is *provably* complete (#19): dialogs were
    # enumerated, no chat failed, and no integrity gaps remain.
    complete = (
        report["enumerated"]
        and report["total"] > 0
        and report["failed"] == 0
        and gaps_left == 0
    )
    if not complete:
        console.print(
            "bootstrap pass incomplete, will retry: "
            f"enumerated={report['enumerated']} total={report['total']} "
            f"failed={report['failed']} gaps={gaps_left} error={report['error']}"
        )
        raise SystemExit(1)

    console.print(
        f"bootstrap pass complete: {report['total']} chats, "
        f"+{report['new_messages']} messages"
    )
    bs.clear_marker()
    bs.uninstall_autostart()


@tg_bootstrap.command("status")
@structured_output_options
def tg_bootstrap_status(as_json: bool, as_yaml: bool):
    """Is the bootstrap still pending, and is autostart armed?"""
    from .. import bootstrap as bs
    from ..db import MessageDB

    state = bs.read_marker()
    with MessageDB() as db:
        payload = {
            "pending": state is not None,
            "autostart_installed": bs.autostart_installed(),
            "options": state or {},
            "messages_indexed": db.count(),
            "chats_indexed": len(db.get_chats()),
        }
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return
    mark = "[green]armed[/green]" if payload["pending"] else "[dim]done / not armed[/dim]"
    console.print(f"bootstrap: {mark} · autostart: {payload['autostart_installed']}")
    console.print(
        f"index: {payload['messages_indexed']} messages, {payload['chats_indexed']} chats"
    )


@tg_bootstrap.command("stop")
def tg_bootstrap_stop():
    """Disarm: remove the marker and the autostart entry."""
    from .. import bootstrap as bs

    bs.clear_marker()
    bs.uninstall_autostart()
    console.print("[green]✓[/green] Bootstrap disarmed.")


@tg_group.command("files")
@click.argument("chat")
@click.option("--download", is_flag=True, help="Download pending files and extract text")
@click.option(
    "--type",
    "kinds",
    multiple=True,
    type=click.Choice(["document", "image", "voice", "video", "audio", "other"]),
    help="Filter by attachment kind (repeatable)",
)
@click.option("--msg-id", "msg_ids", multiple=True, type=int, help="Specific message IDs")
@click.option("--hours", type=int, help="Only attachments within N hours")
@click.option("-n", "--limit", default=20, show_default=True, help="Max files")
@structured_output_options
def tg_files(
    chat: str,
    download: bool,
    kinds: tuple[str, ...],
    msg_ids: tuple[int, ...],
    hours: int | None,
    limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """List attachments of CHAT from the local index; --download fetches them.

    Listing is offline. --download connects to Telegram, stores files under
    <data_dir>/files/<chat_id>/ and extracts text (pdf, docx, xlsx, pptx,
    csv…) into a sibling .txt whose path lands in text_path. Images are
    downloaded as-is — read them with your own vision. Voice files are
    downloaded but not transcribed (v2).
    """
    from ..db import MessageDB

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat_id is None:
            return

        if download:
            from ..client import download_attachments

            async def _run():
                async with connect() as client:
                    return await download_attachments(
                        client,
                        db,
                        _parse_chat(chat) if str(chat).lstrip("-").isdigit() else chat,
                        msg_ids=list(msg_ids) or None,
                        kinds=list(kinds) or None,
                        hours=hours,
                        limit=limit,
                    )

            downloaded = _run_async(_run())
            if emit_structured(downloaded, as_json=as_json, as_yaml=as_yaml):
                return
            if not downloaded:
                console.print("[yellow]Nothing new to download.[/yellow]")
                return
            for d in downloaded:
                extra = f" → text: {d['text_path']}" if d["text_path"] else ""
                console.print(f"[green]✓[/green] {d['local_path']}{extra}")
            console.print(f"\n[dim]{len(downloaded)} files downloaded[/dim]")
            return

        rows = db.get_attachments(
            chat_id=chat_id,
            hours=hours,
            kind=kinds[0] if len(kinds) == 1 else None,
            limit=limit,
        )
        if len(kinds) > 1:
            rows = [r for r in rows if r["kind"] in kinds]
        if msg_ids:
            rows = [r for r in rows if r["msg_id"] in msg_ids]

    if emit_structured(rows, as_json=as_json, as_yaml=as_yaml):
        return
    if not rows:
        console.print("[yellow]No attachments in local index for this chat.[/yellow]")
        return
    for r in rows:
        ts = (r.get("timestamp") or "")[:10]
        size = r.get("size_bytes") or 0
        state = "✓" if r.get("local_path") else "·"
        name = r.get("file_name") or f"{r['kind']}_{r['msg_id']}"
        console.print(
            f" {state} [dim]{ts}[/dim] [{r['kind']}] {name}"
            f" ({size // 1024} KB) msg:{r['msg_id']}"
        )
    console.print(f"\n[dim]{len(rows)} attachments (✓ = downloaded)[/dim]")


@tg_group.command("edit")
@click.argument("chat")
@click.argument("msg_id", type=int)
@click.argument("new_text")
@click.option("--no-preview", is_flag=True, help="Disable link preview")
@structured_output_options
def tg_edit(chat: str, msg_id: int, new_text: str, no_preview: bool, as_json: bool, as_yaml: bool):
    """Edit a previously sent message. CHAT MSG_ID NEW_TEXT."""

    async def _run():
        async with connect() as client:
            result = await client.edit_message(
                _parse_chat(chat),
                msg_id,
                new_text,
                link_preview=not no_preview,
            )
            # Mirror the remote edit into the local index right away (#24):
            # FTS refreshes via the content-update trigger.
            from ..client import marked_peer_id

            entity = await client.get_entity(_parse_chat(chat))
            with MessageDB() as db:
                db.conn.execute(
                    "UPDATE messages SET content = ? WHERE chat_id = ? AND msg_id = ?",
                    (new_text, marked_peer_id(entity), msg_id),
                )
                db.conn.commit()
            return result

    _run_async(_run())
    payload = {"edited": True, "msg_id": msg_id, "chat": chat}
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(f"[green]\u2713[/green] Message {msg_id} edited")


@tg_group.command("delete")
@click.argument("chat")
@click.argument("msg_ids", nargs=-1, type=int, required=True)
@structured_output_options
def tg_delete(chat: str, msg_ids: tuple[int, ...], as_json: bool, as_yaml: bool):
    """Delete one or more messages. CHAT MSG_ID [MSG_ID ...]."""

    async def _run():
        async with connect() as client:
            await client.delete_messages(_parse_chat(chat), list(msg_ids))
            # Cascade the deletion locally: messages, FTS, links, attachments
            # and downloaded files (#24).
            from ..client import marked_peer_id, remove_local_files

            entity = await client.get_entity(_parse_chat(chat))
            with MessageDB() as db:
                res = db.delete_messages(marked_peer_id(entity), list(msg_ids))
            remove_local_files(res["files"])

    _run_async(_run())
    payload = {"deleted": True, "msg_ids": list(msg_ids), "chat": chat}
    if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(f"[green]\u2713[/green] Deleted {len(msg_ids)} message(s)")
