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
    from ..hostapps import uv_tool_tg_path

    return uv_tool_tg_path()


# ─────────────────────── tg connect ───────────────────────


_PERPLEXITY_STEPS = (
    "Perplexity only accepts connectors through its own UI (macOS app):\n"
    "  1. Perplexity → Settings → Connectors\n"
    "  2. Install the helper app (PerplexityXPC) when prompted\n"
    "  3. Add Connector → Advanced tab\n"
    "  4. Server Name: tg, then paste the JSON below\n"
    "  5. Save, wait for the Running state, then enable tg under Sources"
)


def _resolve_command_path(command: str | None) -> Path:
    from ..hostapps import tg_binary_path

    if command:
        p = Path(command).expanduser().resolve()
        if not p.exists():
            console.print(f"[red]✗ {p} does not exist[/red]")
            raise SystemExit(1)
        return p
    try:
        return tg_binary_path()
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1) from None


def _announce(app: str, config_path: str) -> None:
    console.print(f"[bold]{app}[/bold]: will add a read-only MCP entry to {config_path}")


def _connect_one(app: str, tg_path: Path, force: bool = False) -> dict:
    from .. import hostapps

    if app == "claude-desktop":
        return hostapps.connect_claude_desktop(tg_path, force=force)
    if app == "codex":
        return hostapps.connect_codex(tg_path)
    raise ValueError(app)


def _print_result(report: dict) -> None:
    label = {"added": "added", "updated": "updated", "already": "already configured"}[
        report["status"]
    ]
    console.print(f"[green]✓[/green] {report['app']}: {label} → {report['config_path']}")
    if report.get("backup"):
        console.print(f"  [dim]previous config saved to {report['backup']}[/dim]")


def _print_selftest(tg_path: Path) -> bool:
    from ..hostapps import bridge_selftest

    console.print("[dim]Testing the bridge (the exact command the app will run)…[/dim]")
    result = bridge_selftest(tg_path)
    if result["ok"]:
        console.print(
            f"[green]✓[/green] bridge OK, {result['tools']} tools"
            f" (server {result['server_version']})"
        )
        return True
    console.print(f"[red]✗ bridge self-test failed: {result['error']}[/red]")
    console.print(
        "[yellow]The app would show a dead server. Fix the tg install first"
        " (try `tg connect status`).[/yellow]"
    )
    return False


def _print_perplexity(tg_path: Path) -> None:
    from ..hostapps import snippet_json

    snippet = snippet_json(tg_path)
    console.print(_PERPLEXITY_STEPS)
    console.print(snippet, markup=False, highlight=False)
    import shutil as _shutil

    if _shutil.which("pbcopy"):
        try:
            subprocess.run(["pbcopy"], input=snippet, text=True, check=True)
            console.print("[dim]Copied to clipboard.[/dim]")
        except (OSError, subprocess.CalledProcessError):
            pass


def _offer_autosync(interactive: bool) -> None:
    """After wiring a desktop app: the bridge only reads, so offer the
    scheduled refresh that keeps the index fresh for it."""
    from .. import autosync as asy

    if asy.schedule_installed():
        console.print("[dim]Autosync already armed (tg autosync status).[/dim]")
        return
    if not asy.schedule_supported():
        console.print(
            "[dim]Keep the index fresh with a scheduled `tg autosync run`"
            " (Task Scheduler on Windows); see docs/DESKTOP-APPS.md.[/dim]"
        )
        return
    if not interactive:
        console.print("[dim]Keep the index fresh for apps: tg autosync start[/dim]")
        return
    console.print(
        "\nThe bridge reads the local index; a scheduled refresh keeps it fresh."
    )
    if click.confirm(
        "Refresh the index automatically (tg autosync, every 15 min)?", default=True
    ):
        from .tg import tg_autosync_start

        ctx = click.get_current_context()
        try:
            ctx.invoke(tg_autosync_start, interval=None, limit=2000, delay=1.0)
        except SystemExit:
            # A failed schedule install must not abort the surrounding
            # wizard or connect flow; the command already explained why.
            console.print("[dim]Later: tg autosync start[/dim]")
    else:
        console.print("[dim]Later: tg autosync start[/dim]")


def _restart_hint(app: str) -> None:
    hints = {
        "claude-desktop": "Fully restart Claude Desktop (Cmd+Q, reopen) to load it.",
        "codex": (
            "Restart the ChatGPT desktop app or Codex session to load it."
            " Works in both chat and Codex modes."
        ),
    }
    console.print(f"  [dim]{hints[app]}[/dim]")


@system_group.group("connect", invoke_without_command=True)
@click.option("--yes", is_flag=True, help="Connect every detected app without asking")
@click.option("--command", "command_path", default=None, help="Absolute path to tg")
@click.pass_context
def connect_group(ctx: click.Context, yes: bool, command_path: str | None):
    """Wire the read-only MCP bridge into desktop chat apps.

    Claude Desktop and Codex/ChatGPT desktop are configured automatically;
    Perplexity gets a copy-paste snippet for its Connectors UI.
    """
    if ctx.invoked_subcommand is not None:
        return
    from ..hostapps import detect_apps

    tg_path = _resolve_command_path(command_path)
    detected = detect_apps()
    any_available = False
    connected: list[str] = []

    for app in ("claude-desktop", "codex"):
        if not detected[app]["detected"]:
            continue
        any_available = True
        _announce(app, detected[app]["config_path"])
        if not yes and not click.confirm(f"Connect {app}?", default=True):
            continue
        try:
            report = _connect_one(app, tg_path)
        except RuntimeError as e:
            console.print(f"[red]✗ {app}: {e}[/red]")
            continue
        _print_result(report)
        _restart_hint(app)
        connected.append(app)

    if detected["perplexity"]["detected"]:
        any_available = True
        console.print("\n[bold]perplexity[/bold]: detected")
        _print_perplexity(tg_path)

    if not any_available:
        console.print(
            "No desktop chat apps detected. Supported: Claude Desktop,"
            " Codex/ChatGPT desktop, Perplexity. Snippets: tg connect manual"
        )
        return
    if connected:
        _print_selftest(tg_path)
        _offer_autosync(interactive=not yes)


@connect_group.command("claude-desktop")
@click.option("--force", is_flag=True, help="Create the config directory if missing")
@click.option("--command", "command_path", default=None, help="Absolute path to tg")
@structured_output_options
def connect_claude_cmd(force: bool, command_path: str | None, as_json: bool, as_yaml: bool):
    """Add the tg bridge to Claude Desktop's MCP config."""
    tg_path = _resolve_command_path(command_path)
    from ..hostapps import connect_claude_desktop

    try:
        report = connect_claude_desktop(tg_path, force=force)
    except RuntimeError as e:
        if emit_error("connect_failed", str(e)):
            raise SystemExit(1) from None
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1) from None
    report["selftest"] = None
    if not as_json and not as_yaml:
        _print_result(report)
        _restart_hint("claude-desktop")
        _print_selftest(tg_path)
        return
    from ..hostapps import bridge_selftest

    report["selftest"] = bridge_selftest(tg_path)
    emit_structured(report, as_json=as_json, as_yaml=as_yaml)


@connect_group.command("codex")
@click.option("--command", "command_path", default=None, help="Absolute path to tg")
@structured_output_options
def connect_codex_cmd(command_path: str | None, as_json: bool, as_yaml: bool):
    """Add the tg bridge to Codex/ChatGPT desktop (~/.codex/config.toml)."""
    tg_path = _resolve_command_path(command_path)
    from ..hostapps import connect_codex

    try:
        report = connect_codex(tg_path)
    except RuntimeError as e:
        if emit_error("connect_failed", str(e)):
            raise SystemExit(1) from None
        console.print(f"[red]✗ {e}[/red]")
        raise SystemExit(1) from None
    if not as_json and not as_yaml:
        _print_result(report)
        _restart_hint("codex")
        _print_selftest(tg_path)
        return
    from ..hostapps import bridge_selftest

    report["selftest"] = bridge_selftest(tg_path)
    emit_structured(report, as_json=as_json, as_yaml=as_yaml)


@connect_group.command("perplexity")
@click.option("--command", "command_path", default=None, help="Absolute path to tg")
def connect_perplexity_cmd(command_path: str | None):
    """Print the Perplexity Connectors walkthrough and JSON snippet."""
    tg_path = _resolve_command_path(command_path)
    _print_perplexity(tg_path)


@connect_group.command("manual")
@click.option("--command", "command_path", default=None, help="Absolute path to tg")
def connect_manual_cmd(command_path: str | None):
    """Print generic JSON and TOML snippets for any MCP-capable host."""
    from ..hostapps import snippet_json, snippet_toml

    tg_path = _resolve_command_path(command_path)
    console.print("JSON (mcpServers-style hosts, Perplexity Advanced tab):")
    console.print(snippet_json(tg_path), markup=False, highlight=False)
    console.print("\nTOML (~/.codex/config.toml):")
    console.print(snippet_toml(tg_path), markup=False, highlight=False)


@connect_group.command("status")
@structured_output_options
def connect_status_cmd(as_json: bool, as_yaml: bool):
    """Show detection and configuration state per desktop app."""
    from ..hostapps import detect_apps

    report = detect_apps()
    if emit_structured(report, as_json=as_json, as_yaml=as_yaml):
        return
    from rich.table import Table

    table = Table(title="Desktop apps")
    table.add_column("app")
    table.add_column("detected")
    table.add_column("configured")
    table.add_column("config path")
    for app, info in report.items():
        configured = "yes" if info["configured"] else "no"
        if info["broken"]:
            configured = "[red]broken (tg path missing)[/red]"
        table.add_row(
            app,
            "yes" if info["detected"] else "no",
            configured,
            info["config_path"] or "(UI only)",
        )
    console.print(table)
    if any(info["broken"] for info in report.values()):
        console.print("[yellow]Fix broken entries by rerunning tg connect.[/yellow]")


# ─────────────────────── tg setup ───────────────────────


_MY_TELEGRAM_HINT = (
    "Built-in Telegram Desktop keys are used by default and work out of the box.\n"
    "Your own keys from [link]https://my.telegram.org[/link] are recommended for"
    " heavy syncing.\n"
    "Creating your own app there? Two pitfalls:\n"
    "  - the word \"telegram\" in any form field makes the site fail with a bare ERROR\n"
    "  - App title, Short name and Platform are required; URL may stay empty"
)

_HASH_RE = r"[0-9a-f]{32}"


def _write_env_pair(env_path: Path, api_id: int, api_hash: str) -> None:
    """Replace any TG_API_ID/TG_API_HASH lines, keep the rest, write atomically."""
    from ..config import harden_path

    kept: list[str] = []
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("TG_API_ID=", "TG_API_HASH=")):
                continue
            kept.append(line)
    kept.extend([f"TG_API_ID={api_id}", f"TG_API_HASH={api_hash}"])
    tmp = env_path.with_name(env_path.name + ".tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    harden_path(tmp)
    tmp.replace(env_path)
    harden_path(env_path)


def _step_credentials(
    env_path: Path, api_id: int | None, api_hash: str | None, yes: bool
) -> str:
    """Wizard step 1. Returns 'custom' | 'builtin' | 'existing'.

    Built-in keys are the default: choosing them writes nothing to .env
    (absence means built-ins; env vars still override).
    """
    import os as _os
    import re

    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    env_has_id = bool(re.search(r"^TG_API_ID=\d+", existing, re.MULTILINE))
    env_has_hash = bool(re.search(r"^TG_API_HASH=\S+", existing, re.MULTILINE))
    proc_has_id = bool(_os.environ.get("TG_API_ID"))
    proc_has_hash = bool(_os.environ.get("TG_API_HASH"))
    has_id = env_has_id or proc_has_id
    has_hash = env_has_hash or proc_has_hash

    if has_id and has_hash and api_id is None and api_hash is None:
        if env_has_id and env_has_hash:
            console.print("[green]✓[/green] Credentials already configured")
            return "existing"
        # Keys live only in the process environment (shell export or
        # TG_ENV_FILE). Scheduled workers (autosync, bootstrap) start with
        # a scrubbed environment and would fall back to the built-in keys.
        console.print(
            "[yellow]Credentials come from the shell environment, not from"
            f" {env_path}; background workers would not see them.[/yellow]"
        )
        raw_id = _os.environ.get("TG_API_ID", "")
        raw_hash = _os.environ.get("TG_API_HASH", "")
        if (
            not yes
            and raw_id.isdigit()
            and re.fullmatch(_HASH_RE, raw_hash)
            and click.confirm("Persist them into the data dir .env?", default=True)
        ):
            _write_env_pair(env_path, int(raw_id), raw_hash)
            console.print(f"[green]✓[/green] Credentials written to {env_path}")
        return "existing"
    if has_id != has_hash and api_id is None and api_hash is None:
        console.print(
            "[red]TG_API_ID and TG_API_HASH must be set together. Add the missing"
            f" one to {env_path} or remove the other, then rerun tg setup.[/red]"
        )
        raise SystemExit(1)

    console.print(f"\n[bold]1. API credentials[/bold]\n{_MY_TELEGRAM_HINT}")

    if (api_id is None) != (api_hash is None):
        if yes:
            console.print(
                "[red]--api-id and --api-hash must be given together"
                " (or neither to use built-in keys)[/red]"
            )
            raise SystemExit(1)
        # Interactive: prompt only for the missing half below.

    if api_hash is not None:
        api_hash = api_hash.strip()

    if yes:
        if api_id is None and api_hash is None:
            console.print(
                "Using built-in Telegram Desktop keys."
                " Pass --api-id and --api-hash to use your own."
            )
            return "builtin"
        if not re.fullmatch(_HASH_RE, api_hash or ""):
            console.print("[red]api_hash must be 32 lowercase hex characters[/red]")
            raise SystemExit(1)
    else:
        def _builtin() -> None:
            console.print(
                "[green]✓[/green] Using built-in keys"
                " (rerun tg setup anytime to switch to your own)"
            )

        if api_id is None:
            raw = click.prompt(
                "api_id (press Enter to use built-in keys)",
                default="",
                show_default=False,
            ).strip()
            while raw and not raw.isdigit():
                console.print("[yellow]api_id must be a number, try again[/yellow]")
                raw = click.prompt(
                    "api_id (press Enter to use built-in keys)",
                    default="",
                    show_default=False,
                ).strip()
            if not raw:
                _builtin()
                return "builtin"
            api_id = int(raw)
        hash_prompt = "api_hash (32 hex characters, or Enter to use built-in keys)"
        if api_hash is None:
            api_hash = click.prompt(hash_prompt, default="", show_default=False).strip()
        while not re.fullmatch(_HASH_RE, api_hash or ""):
            if not api_hash:
                # The my.telegram.org attempt failed midway? Enter falls
                # back to built-ins instead of trapping the user here.
                _builtin()
                return "builtin"
            console.print(
                "[yellow]api_hash must be 32 lowercase hex characters, try again[/yellow]"
            )
            api_hash = click.prompt(hash_prompt, default="", show_default=False).strip()

    shadowed = (
        proc_has_id and not env_has_id and _os.environ.get("TG_API_ID") != str(api_id)
    ) or (
        proc_has_hash and not env_has_hash and _os.environ.get("TG_API_HASH") != api_hash
    )
    _write_env_pair(env_path, api_id, api_hash)
    # .env was already loaded at import time — export the new pair so the
    # sign-in step of THIS wizard run uses it, not the built-in keys.
    _os.environ["TG_API_ID"] = str(api_id)
    _os.environ["TG_API_HASH"] = api_hash
    console.print(f"[green]✓[/green] Credentials written to {env_path}")
    if shadowed:
        console.print(
            "[yellow]A shell export or TG_ENV_FILE holds different keys and"
            " will shadow the .env in future runs; update or remove it.[/yellow]"
        )
    return "custom"


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
@click.option(
    "--apps",
    default=None,
    help=(
        "Desktop chat apps to wire over MCP: claude-desktop,codex or none"
        " (default: detect and ask; silently skipped under --yes)"
    ),
)
def setup_cmd(
    yes: bool,
    api_id: int | None,
    api_hash: str | None,
    agents: str | None,
    skip_login: bool,
    skip_bootstrap: bool,
    apps: str | None,
):
    """Interactive setup wizard: credentials, sign-in, agent skill, first sync."""
    import asyncio

    from rich.panel import Panel
    from rich.table import Table

    from .. import skillpkg
    from ..client import check_auth
    from ..config import get_data_dir

    console.print(
        Panel.fit(
            "[bold]tg setup[/bold] — Telegram as context for coding agents\n"
            "Steps: credentials → sign-in → agent skill → desktop apps → initial sync",
            border_style="cyan",
        )
    )

    # 1. Credentials -------------------------------------------------------
    data_dir = get_data_dir()
    env_path = data_dir / ".env"
    _step_credentials(env_path, api_id, api_hash, yes)

    # 2. Sign-in -----------------------------------------------------------
    if not skip_login:
        info = asyncio.run(check_auth())
        if info["authenticated"]:
            console.print(f"[green]✓[/green] Signed in as id {info.get('id')}")
        elif info.get("config_error"):
            console.print(f"[red]✗ Configuration error: {info['error']}[/red]")
            raise SystemExit(1)
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
        console.print("\n[bold]3. Agent skill[/bold]")
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

    # 4. Desktop apps ------------------------------------------------------
    app_results, offer_autosync = _step_desktop_apps(apps, yes)

    # 5. Initial sync ------------------------------------------------------
    if not skip_bootstrap and not yes:
        console.print(
            "\n[bold]5. Initial sync[/bold] — pulls your chats into the local index."
            " Big accounts take hours; it survives reboots and removes itself"
            " when done."
        )
        if click.confirm("Start it now?", default=False):
            from .tg import tg_bootstrap_start

            ctx = click.get_current_context()
            ctx.invoke(tg_bootstrap_start, delay=2.0, limit=5000)
        else:
            console.print("[dim]Later: tg bootstrap start[/dim]")

    # Autosync is offered AFTER bootstrap on purpose: with the marker
    # already written, the first autosync tick steps aside instead of
    # racing the initial sync for the single Telethon session.
    if offer_autosync:
        _offer_autosync(interactive=not yes)

    # 6. Summary -----------------------------------------------------------
    from ..update import current_version, detect_install

    table = Table(title="Setup summary", show_header=False)
    table.add_row("version", current_version())
    table.add_row("install", detect_install())
    table.add_row("data dir", str(data_dir))
    table.add_row("skill", skillpkg.skill_status().get("mode", "not installed"))
    if app_results:
        table.add_row(
            "desktop apps",
            ", ".join(f"{app}: {state}" for app, state in app_results.items()),
        )
    table.add_row("next", "tg chats · tg brief <chat> · tg search '…'")
    console.print(table)

    # Explicitly requested apps that failed to wire must be visible to
    # automation: full report above, non-zero exit here.
    if apps not in (None, "none") and any(
        state == "failed" for state in app_results.values()
    ):
        raise SystemExit(1)


def _step_desktop_apps(apps: str | None, yes: bool) -> tuple[dict[str, str], bool]:
    """Wizard step 4: wire desktop chat apps over the MCP bridge.

    apps=None means detect and ask (silently skipped under --yes);
    an explicit list connects without prompting; 'none' skips.
    Returns (per-app results, whether to offer autosync after bootstrap).
    """
    if apps == "none" or (yes and apps is None):
        return {}, False

    from ..hostapps import detect_apps, tg_binary_path

    results: dict[str, str] = {}
    detected = detect_apps()

    if apps is not None:
        wanted = [a.strip() for a in apps.split(",") if a.strip()]
        bad = [a for a in wanted if a not in ("claude-desktop", "codex")]
        if bad:
            console.print(
                f"[red]--apps: unknown app(s) {', '.join(bad)};"
                " supported: claude-desktop,codex or none[/red]"
            )
            raise SystemExit(1)
    else:
        wanted = [
            a for a in ("claude-desktop", "codex") if detected[a]["detected"]
        ]

    show_perplexity = apps is None and detected["perplexity"]["detected"]
    if not wanted and not show_perplexity:
        console.print(
            "\n[dim]4. Desktop apps: none detected. Later: tg connect[/dim]"
        )
        return {}, False

    console.print(
        "\n[bold]4. Desktop apps[/bold] — tg can serve your Telegram index to"
        " desktop chat apps over MCP (read-only)."
    )
    try:
        tg_path = tg_binary_path()
    except RuntimeError as e:
        # A sub-step must not kill the wizard: skip, keep steps 5-6 alive.
        console.print(
            f"[yellow]⚠ {e}[/yellow]\n"
            "[dim]Skipping desktop apps. Later: tg connect --command"
            " /abs/path/to/tg[/dim]"
        )
        return dict.fromkeys(wanted, "skipped"), False

    connected = False
    for app in wanted:
        _announce(app, detected[app]["config_path"])
        if apps is None and not click.confirm(f"Connect {app}?", default=True):
            results[app] = "skipped"
            continue
        try:
            report = _connect_one(app, tg_path)
        except RuntimeError as e:
            console.print(f"[red]✗ {app}: {e}[/red]")
            if app == "claude-desktop" and "--force" in str(e):
                console.print("[dim]Retry later: tg connect claude-desktop --force[/dim]")
            results[app] = "failed"
            continue
        _print_result(report)
        _restart_hint(app)
        results[app] = report["status"]
        connected = True

    if show_perplexity:
        console.print("\n[bold]perplexity[/bold]: detected")
        _print_perplexity(tg_path)
        results["perplexity"] = "manual"

    if connected:
        _print_selftest(tg_path)
    return results, connected
