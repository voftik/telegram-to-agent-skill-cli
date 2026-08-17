"""Query commands — search, stats, top, timeline, today, filter."""

import asyncio
from collections import defaultdict

import click
from rich.markup import escape
from rich.table import Table

from ..console import console
from ..db import MessageDB
from ._chat import resolve_chat_id_or_print
from ._output import emit_error, emit_structured, structured_output_options
from ._sync import sync_all_dialogs, sync_chat_dialog


@click.group("query")
def query_group():
    """Query and analysis commands (registered at top-level)."""


def _maybe_sync_first(chat: str | None, sync_first: bool, sync_limit: int) -> None:
    """Refresh local cache before running a query command."""
    if not sync_first:
        return

    if chat:
        with MessageDB() as db:
            matches = db.find_chats(chat)
        if len(matches) > 1:
            return
        asyncio.run(sync_chat_dialog(chat, limit=sync_limit))
        return

    asyncio.run(sync_all_dialogs(limit=sync_limit))


@query_group.command("search")
@click.argument("keyword")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option("-s", "--sender", help="Filter by sender name")
@click.option("--hours", type=click.IntRange(min=1), help="Only search messages within N hours")
@click.option("--regex", "use_regex", is_flag=True, help="Treat KEYWORD as a regex pattern")
@click.option("--sync-first", is_flag=True, help="Refresh local cache before searching")
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@click.option("-n", "--limit", type=click.IntRange(min=1), default=50, help="Max results")
@structured_output_options
def search(
    keyword: str,
    chat: str | None,
    sender: str | None,
    hours: int | None,
    use_regex: bool,
    sync_first: bool,
    sync_limit: int,
    limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Search messages by KEYWORD with optional chat, sender, and time filters."""
    import re

    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        try:
            if use_regex:
                regex_res = db.search_regex(
                    keyword, chat_id=chat_id, sender=sender, hours=hours, limit=limit
                )
                results = regex_res["results"]
                if regex_res["truncated"]:
                    console.print(
                        f"[yellow]⚠ regex scan capped at {db.MAX_REGEX_SCAN} rows —"
                        " narrow the query with -c/--hours[/yellow]"
                    )
            else:
                results = db.search(
                    keyword,
                    chat_id=chat_id,
                    sender=sender,
                    hours=hours,
                    limit=limit,
                )
        except re.error as exc:
            if emit_error("invalid_regex", f"Invalid regex pattern: {exc}"):
                raise SystemExit(1) from None
            console.print(f"[red]Invalid regex pattern: {escape(str(exc))}[/red]")
            raise SystemExit(1) from None

    if results and emit_structured(results, as_json=as_json, as_yaml=as_yaml):
        return

    if not results:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]No messages found.[/yellow]")
        return

    for msg in results:
        ts = (msg.get("timestamp") or "")[:19]
        # NB: do not shadow the `sender` filter argument with row data (#38)
        row_sender = msg.get("sender_name") or "Unknown"
        chat_name = msg.get("chat_name") or ""
        content = (msg.get("content") or "")[:200]
        console.print(
            f"[dim]{ts}[/dim] [cyan]{escape(chat_name)}[/cyan] |"
            f" [bold]{escape(row_sender)}[/bold]: {escape(content)}"
        )

    filters = []
    if chat:
        filters.append(f"chat={chat}")
    if sender:
        filters.append(f"sender={sender}")
    if hours:
        filters.append(f"hours={hours}")
    if use_regex:
        filters.append("mode=regex")
    suffix = f" ({', '.join(filters)})" if filters else ""
    console.print(f"\n[dim]Found {len(results)} messages{suffix}[/dim]")


@query_group.command("brief")
@click.argument("chat")
@click.option("--sync-first", is_flag=True, help="Refresh this chat before building the brief")
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@structured_output_options
def brief(chat: str, sync_first: bool, sync_limit: int, as_json: bool, as_yaml: bool):
    """Chat passport: volume, activity spikes, top senders, files and links.

    Agents should call this before deep-reading a chat to pick a sane depth.
    """
    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat_id is None:
            return
        matches = db.find_chats(chat)
        info = db.brief(chat_id)

    info["chat_id"] = chat_id
    info["chat_name"] = matches[0]["chat_name"] if matches else chat
    if emit_structured(info, as_json=as_json, as_yaml=as_yaml):
        return

    console.print(f"[bold cyan]{escape(info['chat_name'] or '')}[/bold cyan] (id: {chat_id})")
    console.print(
        f"  messages: [bold]{info['total']}[/bold]"
        f"  (7d: {info['msgs_7d']}, 30d: {info['msgs_30d']})"
    )
    console.print(f"  period: {(info['first_msg'] or '')[:10]} … {(info['last_msg'] or '')[:10]}")
    if info["top_days"]:
        days = ", ".join(f"{d['day']} ({d['msg_count']})" for d in info["top_days"])
        console.print(f"  peak days: {days}")
    if info["top_senders"]:
        senders = ", ".join(
            f"{escape(s['sender_name'] or '')} ({s['msg_count']})"
            for s in info["top_senders"]
        )
        console.print(f"  top senders: {senders}")
    if info["attachments"]:
        att = ", ".join(f"{k}: {v}" for k, v in sorted(info["attachments"].items()))
        console.print(f"  attachments: {att}")
    if info["links"]:
        lnk = ", ".join(f"{k}: {v}" for k, v in sorted(info["links"].items()))
        console.print(f"  links: {lnk}")


@query_group.command("links")
@click.argument("chat", required=False)
@click.option("--hours", type=click.IntRange(min=1), help="Only links within N hours")
@click.option(
    "--kind",
    type=click.Choice(["gdoc", "gsheet", "gslides", "tme", "web"]),
    help="Filter by link kind",
)
@click.option(
    "-n",
    "--limit",
    type=click.IntRange(min=1),
    default=100,
    show_default=True,
    help="Max results",
)
@structured_output_options
def links_cmd(
    chat: str | None,
    hours: int | None,
    kind: str | None,
    limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Links shared in chats, with agent-fetchable fetch_url.

    For Google Docs/Sheets/Slides fetch_url points at the export endpoint —
    fetch that, not the original url. kind=tme resolves via `tg thread`.
    """
    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        results = db.get_links(chat_id=chat_id, hours=hours, kind=kind, limit=limit)

    if emit_structured(results, as_json=as_json, as_yaml=as_yaml):
        return
    if not results:
        console.print("[yellow]No links found.[/yellow]")
        return
    for r in results:
        ts = (r.get("timestamp") or "")[:16]
        console.print(
            f"[dim]{ts}[/dim] [cyan]{escape(r.get('chat_name') or '')}[/cyan]"
            f" \\[{r['kind']}] {escape(r['url'])}"
        )
    console.print(f"\n[dim]{len(results)} links[/dim]")


@query_group.command("thread")
@click.argument("chat", required=False)
@click.option("--msg-id", type=int, help="Message ID inside the thread")
@click.option("--url", "tme_url", help="t.me/c/… link to a message")
@structured_output_options
def thread_cmd(
    chat: str | None,
    msg_id: int | None,
    tme_url: str | None,
    as_json: bool,
    as_yaml: bool,
):
    """Reconstruct the reply thread around one message, in chronological order."""
    if tme_url:
        from ..links import parse_tme_message_link

        parsed = parse_tme_message_link(tme_url)
        if parsed is None:
            message = (
                "Not a Telegram message link. Supported: t.me/c/<id>/<msg>,"
                " t.me/<username>/<msg> (incl. topic forms)."
            )
            if emit_error("bad_url", message):
                raise SystemExit(1) from None
            console.print(f"[red]{message}[/red]")
            raise SystemExit(1)
        chat_ref, msg_id = parsed
        if isinstance(chat_ref, int):
            resolved_chat_id = chat_ref
        else:
            # Public link: resolve the username against local chat names.
            with MessageDB() as db:
                resolved = resolve_chat_id_or_print(db, chat_ref)
            if resolved is None:
                return
            resolved_chat_id = resolved
    else:
        if not chat or msg_id is None:
            if emit_error("missing_args", "Provide CHAT and --msg-id, or --url."):
                raise SystemExit(1) from None
            console.print("[red]Provide CHAT and --msg-id, or --url.[/red]")
            return
        with MessageDB() as db:
            resolved = resolve_chat_id_or_print(db, chat)
        if resolved is None:
            return
        resolved_chat_id = resolved

    with MessageDB() as db:
        msgs = db.get_thread(resolved_chat_id, msg_id)

    if emit_structured(msgs, as_json=as_json, as_yaml=as_yaml):
        return
    if not msgs:
        console.print("[yellow]Thread not found in local cache. Sync the chat first.[/yellow]")
        return
    for m2 in msgs:
        ts = (m2.get("timestamp") or "")[:16]
        sender = m2.get("sender_name") or "Unknown"
        marker = "↳ " if m2.get("reply_to_msg_id") else ""
        console.print(
            f"[dim]{ts}[/dim] {marker}[bold]{escape(sender)}[/bold]:"
            f" {escape((m2.get('content') or '')[:300])}"
        )
    console.print(f"\n[dim]{len(msgs)} messages in thread[/dim]")


@query_group.command("style")
@click.option("-c", "--chat", help="Restrict corpus to one chat")
@click.option(
    "-n",
    "--limit",
    type=click.IntRange(min=1),
    default=500,
    show_default=True,
    help="Max messages",
)
@click.option(
    "--min-len",
    type=click.IntRange(min=0),
    default=15,
    show_default=True,
    help="Skip messages shorter than N characters",
)
@structured_output_options
def style_cmd(
    chat: str | None,
    limit: int,
    min_len: int,
    as_json: bool,
    as_yaml: bool,
):
    """My own outgoing messages — a corpus for reply style calibration."""
    from ..client import load_cached_me

    me = load_cached_me()
    if not me:
        if emit_error(
            "no_identity",
            "Account identity unknown. Run `tg whoami` once, then retry.",
        ):
            raise SystemExit(1) from None
        console.print("[red]Account identity unknown. Run `tg whoami` once, then retry.[/red]")
        return

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        corpus = db.get_style_corpus(
            me["id"], chat_id=chat_id, limit=limit, min_len=min_len
        )

    if emit_structured({"me": me, "count": len(corpus), "messages": corpus},
                       as_json=as_json, as_yaml=as_yaml):
        return
    if not corpus:
        console.print("[yellow]No own messages in local cache yet.[/yellow]")
        return
    for m3 in corpus[:50]:
        stamp = (m3.get("timestamp") or "")[:10]
        console.print(f"[dim]{stamp}[/dim] {escape(m3['content'][:200])}")
    console.print(f"\n[dim]{len(corpus)} messages (showing up to 50; use --yaml for all)[/dim]")


@query_group.command("recent")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option("-s", "--sender", help="Filter by sender name")
@click.option(
    "--hours",
    type=click.IntRange(min=1),
    default=24,
    show_default=True,
    help="Only show last N hours",
)
@click.option(
    "--sync-first",
    is_flag=True,
    help="Refresh local cache before reading recent messages",
)
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@click.option("-n", "--limit", type=click.IntRange(min=1), default=50, help="Max messages")
@structured_output_options
def recent(
    chat: str | None,
    sender: str | None,
    hours: int,
    sync_first: bool,
    sync_limit: int,
    limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Show recent messages for browsing without a keyword search."""

    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        msgs = db.get_recent(chat_id=chat_id, sender=sender, hours=hours, limit=limit)

    if msgs and emit_structured(msgs, as_json=as_json, as_yaml=as_yaml):
        return

    if not msgs:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]No recent messages found.[/yellow]")
        return

    for msg in msgs:
        ts = (msg.get("timestamp") or "")[:19]
        sender_name = msg.get("sender_name") or "Unknown"
        chat_name = msg.get("chat_name") or ""
        content = (msg.get("content") or "")[:200].replace("\n", " ")
        console.print(
            f"[dim]{ts}[/dim] [cyan]{escape(chat_name)}[/cyan] |"
            f" [bold]{escape(sender_name)}[/bold]: {escape(content)}"
        )

    filters = [f"hours={hours}"]
    if chat:
        filters.append(f"chat={chat}")
    if sender:
        filters.append(f"sender={sender}")
    console.print(f"\n[dim]Showing {len(msgs)} recent messages ({', '.join(filters)})[/dim]")


@query_group.command("stats")
@click.option("--sync-first", is_flag=True, help="Refresh local cache before calculating stats")
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@structured_output_options
def stats(sync_first: bool, sync_limit: int, as_json: bool, as_yaml: bool):
    """Show message statistics per chat."""
    _maybe_sync_first(None, sync_first, sync_limit)

    with MessageDB() as db:
        chats = db.get_chats()
        total = db.count()

    if emit_structured({"total": total, "chats": chats}, as_json=as_json, as_yaml=as_yaml):
        return

    table = Table(title=f"Message Stats (Total: {total})")
    table.add_column("Chat ID", style="dim")
    table.add_column("Chat Name", style="bold")
    table.add_column("Messages", justify="right")
    table.add_column("First Message", style="dim")
    table.add_column("Last Message", style="dim")

    for c in chats:
        table.add_row(
            str(c["chat_id"]),
            c["chat_name"] or "—",
            str(c["msg_count"]),
            (c["first_msg"] or "")[:19],
            (c["last_msg"] or "")[:19],
        )

    console.print(table)


@query_group.command("top")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option("--hours", type=click.IntRange(min=1), help="Only count messages within N hours")
@click.option(
    "--sync-first",
    is_flag=True,
    help="Refresh local cache before calculating top senders",
)
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@click.option("-n", "--limit", type=click.IntRange(min=1), default=20, help="Top N senders")
@structured_output_options
def top(
    chat: str | None,
    hours: int | None,
    sync_first: bool,
    sync_limit: int,
    limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Show most active senders."""
    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        results = db.top_senders(chat_id=chat_id, hours=hours, limit=limit)

    if results and emit_structured(results, as_json=as_json, as_yaml=as_yaml):
        return

    if not results:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]No sender data found.[/yellow]")
        return

    table = Table(title="Top Senders")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Sender", style="bold")
    table.add_column("Messages", justify="right")
    table.add_column("First", style="dim")
    table.add_column("Last", style="dim")

    for i, r in enumerate(results, 1):
        table.add_row(
            str(i),
            r["sender_name"],
            str(r["msg_count"]),
            (r["first_msg"] or "")[:10],
            (r["last_msg"] or "")[:10],
        )

    console.print(table)


@query_group.command("timeline")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option("--hours", type=click.IntRange(min=1), help="Only show last N hours")
@click.option("--by", "granularity", type=click.Choice(["day", "hour"]), default="day")
@click.option(
    "--sync-first",
    is_flag=True,
    help="Refresh local cache before building the timeline",
)
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@structured_output_options
def timeline(
    chat: str | None,
    hours: int | None,
    granularity: str,
    sync_first: bool,
    sync_limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Show message activity over time as a bar chart."""
    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        results = db.timeline(chat_id=chat_id, hours=hours, granularity=granularity)

    if results and emit_structured(results, as_json=as_json, as_yaml=as_yaml):
        return

    if not results:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]No timeline data.[/yellow]")
        return

    max_count = max(r["msg_count"] for r in results)
    bar_width = 40

    for r in results:
        period = r["period"]
        count = r["msg_count"]
        bar_len = int(count / max_count * bar_width) if max_count > 0 else 0
        bar = "█" * bar_len
        console.print(f"[dim]{period}[/dim] {bar} [bold]{count}[/bold]")


@query_group.command("today")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option(
    "--sync-first",
    is_flag=True,
    help="Refresh local cache before reading today's messages",
)
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@structured_output_options
def today(chat: str | None, sync_first: bool, sync_limit: int, as_json: bool, as_yaml: bool):
    """Show today's messages, grouped by chat."""
    from datetime import datetime

    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return
        msgs = db.get_today(chat_id=chat_id)
        latest_ts = db.get_latest_timestamp(chat_id=chat_id)

    if len(msgs) >= 5000:
        # console prints to stderr — the warning never corrupts stdout data
        console.print(
            "[yellow]⚠ showing the first 5000 messages of today —"
            " narrow with -c CHAT[/yellow]"
        )

    if msgs and emit_structured(msgs, as_json=as_json, as_yaml=as_yaml):
        return

    if not msgs:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print("[yellow]No messages today.[/yellow]")
        if latest_ts:
            latest_local = datetime.fromisoformat(latest_ts).astimezone()
            console.print(
                "[dim]Latest local message is from "
                f"{latest_local.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
                "Run 'tg refresh' to refresh.[/dim]"
            )
        else:
            console.print("[dim]Local database is empty. Run 'tg refresh' first.[/dim]")
        return

    # Group by chat
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in msgs:
        grouped[m.get("chat_name") or "Unknown"].append(m)

    for chat_name, chat_msgs in sorted(grouped.items(), key=lambda x: -len(x[1])):
        console.print(
            f"\n[bold cyan]═══ {escape(chat_name)} ({len(chat_msgs)} msgs) ═══[/bold cyan]"
        )
        for m in chat_msgs:
            # Human timestamps in the user's local timezone (#37);
            # structured output keeps explicit UTC offsets.
            try:
                ts = datetime.fromisoformat(m["timestamp"]).astimezone().strftime("%H:%M:%S")
            except (ValueError, KeyError):
                ts = (m.get("timestamp") or "")[11:19]
            sender = m.get("sender_name") or "Unknown"
            content = (m.get("content") or "")[:200].replace("\n", " ")
            console.print(
                f"  [dim]{ts}[/dim] [bold]{escape(sender[:15])}[/bold]: {escape(content)}"
            )

    console.print(f"\n[green]Total: {len(msgs)} messages today[/green]")


@query_group.command("filter")
@click.argument("keywords")
@click.option("-c", "--chat", help="Filter by chat name")
@click.option(
    "--hours",
    type=click.IntRange(min=1),
    help="Only search last N hours (default: today)",
)
@click.option("--sync-first", is_flag=True, help="Refresh local cache before filtering")
@click.option(
    "--sync-limit",
    type=click.IntRange(min=1),
    default=5000,
    show_default=True,
    help="Max messages per chat when using --sync-first",
)
@structured_output_options
def filter_msgs(
    keywords: str,
    chat: str | None,
    hours: int | None,
    sync_first: bool,
    sync_limit: int,
    as_json: bool,
    as_yaml: bool,
):
    """Filter messages by KEYWORDS (comma-separated, OR logic).

    Examples:
        tg filter "Rust,Golang,Java"
        tg filter "招聘,remote,远程" --hours 48
        tg filter "Rust" --chat "牛油果" --json
    """
    import re

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        if emit_error("invalid_keywords", "Please provide at least one keyword."):
            raise SystemExit(1) from None
        console.print("[red]Please provide at least one keyword.[/red]")
        return

    _maybe_sync_first(chat, sync_first, sync_limit)

    with MessageDB() as db:
        chat_id = resolve_chat_id_or_print(db, chat)
        if chat and chat_id is None:
            return

        if hours:
            msgs = db.get_recent(chat_id=chat_id, hours=hours, limit=100000)
            if len(msgs) >= 100000:
                console.print(
                    "[yellow]⚠ scan capped at 100000 messages — narrow the window[/yellow]"
                )
        else:
            msgs = db.get_today(chat_id=chat_id)

    # Filter messages containing ANY of the keywords (case-insensitive)
    pattern = re.compile("|".join(re.escape(k) for k in keyword_list), re.IGNORECASE)
    matched = [m for m in msgs if m.get("content") and pattern.search(m["content"])]

    if not matched:
        if emit_structured([], as_json=as_json, as_yaml=as_yaml):
            return
        console.print(f"[yellow]No messages matching: {', '.join(keyword_list)}[/yellow]")
        return

    if emit_structured(matched, as_json=as_json, as_yaml=as_yaml):
        return

    # Group by chat
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in matched:
        grouped[m.get("chat_name") or "Unknown"].append(m)

    for chat_name, chat_msgs in sorted(grouped.items(), key=lambda x: -len(x[1])):
        console.print(
            f"\n[bold cyan]═══ {escape(chat_name)} ({len(chat_msgs)} matches) ═══[/bold cyan]"
        )
        for m in chat_msgs:
            ts = (m.get("timestamp") or "")[:19]
            sender = m.get("sender_name") or "Unknown"
            content = escape((m.get("content") or "")[:300].replace("\n", " "))
            # Highlight via a callback: the match itself is the replacement,
            # so user text can never inject group references (#37) and the
            # original case is preserved.
            for kw in keyword_list:
                content = re.sub(
                    re.escape(escape(kw)),
                    lambda mm: f"[bold red]{mm.group(0)}[/bold red]",
                    content,
                    flags=re.IGNORECASE,
                )
            console.print(
                f"  [dim]{ts}[/dim] [bold]{escape(sender[:15])}[/bold]: ",
                end="",
            )
            console.print(content, markup=True, highlight=False)

    console.print(
        f"\n[green]Found {len(matched)} messages matching "
        f"'{', '.join(keyword_list)}' "
        f"(from {len(msgs)} total)[/green]"
    )
