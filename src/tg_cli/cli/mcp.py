"""tg mcp — serve the local index as a read-only MCP server (stdio)."""

from __future__ import annotations

import click


@click.group("mcp", invoke_without_command=True)
@click.pass_context
def mcp_group(ctx: click.Context):
    """Serve the local Telegram index as a read-only MCP server over stdio.

    For desktop chat apps that cannot run a CLI: Claude Desktop,
    Perplexity, ChatGPT desktop. Wire it in with `tg connect`.
    Never touches the Telegram session; never sends anything.
    """
    if ctx.invoked_subcommand is not None:
        return
    # stdout is the MCP transport. The passive update hint in main.py is
    # TTY-gated and writes to stderr, so it cannot corrupt the stream —
    # keep it that way if that gate ever changes.
    from ..mcpserver import serve

    raise SystemExit(serve())
