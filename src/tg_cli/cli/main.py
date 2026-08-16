"""tg-cli — Telegram CLI entry point."""

import logging

import click

from .data import data_group
from .query import query_group
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


# Register ALL commands at top-level (flat structure, no `tg tg` nonsense)
for group in (tg_group, query_group, data_group):
    for name, cmd in group.commands.items():
        cli.add_command(cmd, name)
