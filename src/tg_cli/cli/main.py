"""tg-cli — Telegram CLI entry point."""

import logging

import click

from .data import data_group
from .query import query_group
from .system import system_group
from .tg import tg_group


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(package_name="telegram-to-agent-skill-cli")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose: bool):
    """tg — Telegram CLI for syncing chats, searching messages, and local analysis."""
    _setup_logging(verbose)
    # Passive update hint: cache-only, stderr, interactive sessions only.
    import sys as _sys

    if _sys.stdout.isatty():
        ctx = click.get_current_context()

        def _hint():
            try:
                from ..update import passive_hint

                message = passive_hint()
                if message:
                    click.echo(f"hint: {message}", err=True)
            except Exception:  # noqa: BLE001 - a hint must never break a command
                pass

        ctx.call_on_close(_hint)


# Register ALL commands at top-level (flat structure, no `tg tg` nonsense)
for group in (tg_group, query_group, data_group, system_group):
    for name, cmd in group.commands.items():
        cli.add_command(cmd, name)

# `tg mcp` is registered as a group of its own (room for subcommands).
from .mcp import mcp_group  # noqa: E402

cli.add_command(mcp_group, "mcp")
