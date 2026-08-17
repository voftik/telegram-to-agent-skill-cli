"""System commands — skill management, self-update, setup wizard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from ..console import console
from ._output import emit_error, emit_structured, structured_output_options


@click.group("system")
def system_group():
    """System commands (registered at top-level)."""


# ─────────────────────── tg skill ───────────────────────


@system_group.group("skill")
def skill_group():
    """Manage the agent skill installed for Claude Code / Codex."""


@skill_group.command("install")
@click.option("--force", is_flag=True, help="Back up and replace foreign/modified content")
@click.option(
    "--agents",
    default="claude,codex",
    show_default=True,
    help="Comma-separated agents for auto-activation snippets (claude,codex or none)",
)
@click.option(
    "--dev",
    is_flag=True,
    help="Symlink the current checkout's skill instead of copying (development)",
)
@structured_output_options
def skill_install(force: bool, agents: str, dev: bool, as_json: bool, as_yaml: bool):
    """Install the packaged agent skill and auto-activation snippets."""
    from .. import skillpkg
    from ..update import detect_install, editable_checkout

    dev_source = None
    if dev or (detect_install() == "editable" and not force):
        checkout = editable_checkout()
        if checkout:
            candidate = Path(checkout) / "src" / "tg_cli" / "skill"
            if candidate.is_dir():
                dev_source = candidate

    try:
        report = skillpkg.install_skill(force=force, dev_source=dev_source)
    except RuntimeError as e:
        if emit_error("skill_install_failed", str(e)):
            raise SystemExit(1) from None
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1) from None

    agent_set = {a.strip() for a in agents.split(",") if a.strip() and a != "none"}
    report["snippets"] = skillpkg.append_snippets(agent_set)
    if emit_structured(report, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(f"[green]✓[/green] Skill installed ({report['mode']}) → {report['target']}")
    for agent, state in report["snippets"].items():
        console.print(f"  [dim]{agent}: {state}[/dim]")


@skill_group.command("status")
@structured_output_options
def skill_status_cmd(as_json: bool, as_yaml: bool):
    """Show the state of the installed agent skill."""
    from ..skillpkg import skill_status

    status = skill_status()
    if emit_structured(status, as_json=as_json, as_yaml=as_yaml):
        return
    if not status.get("installed"):
        console.print("[yellow]Skill is not installed. Run `tg skill install`.[/yellow]")
        return
    line = f"[green]✓[/green] {status['mode']} → {status.get('target')}"
    if status.get("stale"):
        line += "  [yellow](stale — run `tg skill install --force`)[/yellow]"
    if status.get("modified"):
        line += "  [yellow](locally modified)[/yellow]"
    console.print(line)


@skill_group.command("uninstall")
@structured_output_options
def skill_uninstall(as_json: bool, as_yaml: bool):
    """Remove the managed skill installation."""
    from ..skillpkg import uninstall_skill

    try:
        report = uninstall_skill()
    except RuntimeError as e:
        if emit_error("skill_uninstall_failed", str(e)):
            raise SystemExit(1) from None
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1) from None
    if emit_structured(report, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(f"[green]✓[/green] Removed: {', '.join(report['removed']) or 'nothing'}")


# ─────────────────────── tg update ───────────────────────


@system_group.command("update")
@click.option("--check", "check_only", is_flag=True, help="Only check, change nothing")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation")
@structured_output_options
def update_cmd(check_only: bool, yes: bool, as_json: bool, as_yaml: bool):
    """Update tg to the latest release (PyPI) and refresh the agent skill."""
    from .. import update as upd

    status = upd.update_status(refresh=True)
    kind = upd.detect_install()
    payload = {**status, "install_kind": kind}

    if status["latest"] is None:
        if emit_error("network_unreachable", "Could not reach PyPI to check versions."):
            raise SystemExit(1) from None
        console.print("[red]✗ Could not reach PyPI to check versions.[/red]")
        raise SystemExit(1)

    if check_only:
        if emit_structured(payload, as_json=as_json, as_yaml=as_yaml):
            return
        if status["update_available"]:
            console.print(
                f"[yellow]Update available: {status['current']} → {status['latest']}[/yellow]"
            )
        else:
            console.print(f"[green]✓[/green] Up to date ({status['current']})")
        return

    if not status["update_available"]:
        if emit_structured({**payload, "updated": False}, as_json=as_json, as_yaml=as_yaml):
            return
        console.print(f"[green]✓[/green] Already up to date ({status['current']})")
        return

    command = upd.upgrade_command(kind)
    if command is None:
        guidance = _manual_guidance(kind)
        if emit_error("manual_update_required", guidance, ):
            raise SystemExit(1) from None
        console.print(f"[yellow]{guidance}[/yellow]")
        raise SystemExit(1)

    if not yes and not as_json and not as_yaml:
        console.print(f"Will run: [bold]{' '.join(command)}[/bold]")
        if not click.confirm(f"Update {status['current']} → {status['latest']}?"):
            return

    result = subprocess.run(command)
    if result.returncode != 0:
        if emit_error("update_failed", f"upgrade command exited {result.returncode}"):
            raise SystemExit(1) from None
        console.print(f"[red]✗ upgrade command exited {result.returncode}[/red]")
        raise SystemExit(1)

    # Refresh the skill through the NEW binary — this process still holds
    # the old packaged skill in memory.
    new_tg = _stable_tg_path()
    if new_tg:
        subprocess.run([str(new_tg), "skill", "install", "--force"], check=False)

    done = {**payload, "updated": True}
    if emit_structured(done, as_json=as_json, as_yaml=as_yaml):
        return
    console.print(
        f"[green]✓[/green] Updated to {status['latest']}. Release notes: "
        f"{upd.RELEASES_URL}/v{status['latest']}"
    )


def _manual_guidance(kind: str) -> str:
    from ..update import editable_checkout

    if kind == "editable":
        checkout = editable_checkout() or "<clone>"
        return (
            "Development install detected. Update manually: "
            f"cd {checkout} && git pull && uv tool install --reinstall --editable . "
            "— or switch to the release channel: "
            "uv tool install --force telegram-to-agent-skill-cli && tg skill install --force"
        )
    return "Unknown install kind. Update with your package manager (pip install -U …)."


def _stable_tg_path() -> Path | None:
    """$(uv tool dir)/<package>/bin/tg — stable across upgrades."""
    try:
        out = subprocess.run(
            ["uv", "tool", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    candidate = Path(out) / "telegram-to-agent-skill-cli" / "bin" / "tg"
    return candidate if candidate.exists() else None


# ─────────────────────── tg setup ───────────────────────


@system_group.command("setup")
@click.option("--yes", is_flag=True, help="Non-interactive: accept defaults, fail on gaps")
@click.option("--api-id", type=int, default=None, help="Telegram api_id (my.telegram.org)")
@click.option("--api-hash", default=None, help="Telegram api_hash")
@click.option(
    "--agents",
    default=None,
    help="Agents to integrate: claude,codex or none (default: auto-detect)",
)
@click.option("--skip-login", is_flag=True, help="Skip the Telegram sign-in step")
@click.option("--skip-bootstrap", is_flag=True, help="Do not offer the initial sync")
def setup_cmd(
    yes: bool,
    api_id: int | None,
    api_hash: str | None,
    agents: str | None,
    skip_login: bool,
    skip_bootstrap: bool,
):
    """Interactive setup wizard: credentials, sign-in, agent skill, first sync."""
    import asyncio
    import re

    from rich.panel import Panel
    from rich.table import Table

    from .. import skillpkg
    from ..client import check_auth
    from ..config import get_data_dir, harden_path

    console.print(
        Panel.fit(
            "[bold]tg setup[/bold] — Telegram as context for coding agents\n"
            "Steps: credentials → sign-in → agent skill → initial sync",
            border_style="cyan",
        )
    )

    # 1. Credentials -------------------------------------------------------
    data_dir = get_data_dir()
    env_path = data_dir / ".env"
    existing = env_path.read_text() if env_path.is_file() else ""
    have_creds = "TG_API_ID=" in existing and re.search(r"TG_API_ID=\d+", existing)

    if not have_creds:
        import os as _os

        have_creds = bool(_os.environ.get("TG_API_ID"))
    if not have_creds:
        console.print(
            "\n[bold]1. API credentials[/bold] — create an application at"
            " [link]https://my.telegram.org[/link] → API development tools."
        )
        if api_id is None:
            if yes:
                console.print("[red]--yes given but --api-id/--api-hash missing[/red]")
                raise SystemExit(1)
            api_id = click.prompt("api_id", type=int)
        if api_hash is None:
            if yes:
                console.print("[red]--yes given but --api-hash missing[/red]")
                raise SystemExit(1)
            api_hash = click.prompt("api_hash")
        if not re.fullmatch(r"[0-9a-f]{32}", api_hash or ""):
            console.print("[red]api_hash must be 32 lowercase hex characters[/red]")
            raise SystemExit(1)
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"TG_API_ID={api_id}\nTG_API_HASH={api_hash}\n")
        harden_path(env_path)
        console.print(f"[green]✓[/green] Credentials written to {env_path}")
    else:
        console.print("[green]✓[/green] Credentials already configured")

    # 2. Sign-in -----------------------------------------------------------
    if not skip_login:
        info = asyncio.run(check_auth())
        if info["authenticated"]:
            console.print(f"[green]✓[/green] Signed in as id {info.get('id')}")
        elif not info["reachable"]:
            console.print("[yellow]⚠ Network unreachable, skipping sign-in[/yellow]")
        elif yes:
            console.print("[yellow]Not signed in — run `tg whoami` later.[/yellow]")
        else:
            console.print("\n[bold]2. Sign in[/bold] — code arrives in your Telegram app.")
            from ..client import connect

            async def _login():
                async with connect():
                    return True

            asyncio.run(_login())
            console.print("[green]✓[/green] Signed in")

    # 3. Agent skill -------------------------------------------------------
    if agents is None:
        detected = []
        if (Path.home() / ".claude").is_dir():
            detected.append("claude")
        if (Path.home() / ".codex").is_dir():
            detected.append("codex")
        agents = ",".join(detected) or "claude"
    if agents != "none":
        agent_set = {a.strip() for a in agents.split(",") if a.strip()}
        try:
            report = skillpkg.install_skill(force=False)
        except RuntimeError:
            report = skillpkg.install_skill(force=True)
        snippets = skillpkg.append_snippets(agent_set)
        console.print(
            f"[green]✓[/green] Agent skill installed ({report['mode']}) for:"
            f" {', '.join(sorted(agent_set))}"
        )
        for agent, state in snippets.items():
            console.print(f"  [dim]{agent}: {state}[/dim]")

    # 4. Initial sync ------------------------------------------------------
    if not skip_bootstrap and not yes:
        console.print(
            "\n[bold]4. Initial sync[/bold] — pulls your chats into the local index."
            " Big accounts take hours; it survives reboots and removes itself"
            " when done."
        )
        if click.confirm("Start it now?", default=False):
            from .tg import tg_bootstrap_start

            ctx = click.get_current_context()
            ctx.invoke(tg_bootstrap_start, delay=2.0, limit=5000)
        else:
            console.print("[dim]Later: tg bootstrap start[/dim]")

    # 5. Summary -----------------------------------------------------------
    from ..update import current_version, detect_install

    table = Table(title="Setup summary", show_header=False)
    table.add_row("version", current_version())
    table.add_row("install", detect_install())
    table.add_row("data dir", str(data_dir))
    table.add_row("skill", skillpkg.skill_status().get("mode", "not installed"))
    table.add_row("next", "tg chats · tg brief <chat> · tg search '…'")
    console.print(table)
