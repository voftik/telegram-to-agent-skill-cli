"""Reboot-resilient initial sync.

`tg bootstrap start` arms a per-user autostart entry (macOS LaunchAgent /
Linux systemd user unit) that keeps running `tg bootstrap run` until one
full refresh pass over all dialogs succeeds. Then the worker removes the
marker and uninstalls the autostart entry — after the initial fill the
tool returns to its normal on-demand sync model, no resident daemons.

A busy Telegram session (another sync holds it) makes the worker exit
non-zero; launchd/systemd retries after ~5 minutes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import get_data_dir

LABEL = "dev.tg-cli.bootstrap"
_UNIT_NAME = "tg-cli-bootstrap.service"


# ─────────────────────── state marker ───────────────────────


def marker_path() -> Path:
    return get_data_dir() / "bootstrap-pending.json"


def read_marker() -> dict | None:
    path = marker_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_marker(delay: float, limit: int) -> None:
    marker_path().write_text(json.dumps({"delay": delay, "limit": limit}))


def clear_marker() -> None:
    marker_path().unlink(missing_ok=True)


# ─────────────────────── platform plumbing ───────────────────────


def tg_executable() -> str:
    """Absolute path to the `tg` entry point (launchd has a bare PATH)."""
    candidate = Path(sys.argv[0]).resolve()
    if candidate.name in ("tg", "tg.exe") and candidate.exists():
        return str(candidate)
    return shutil.which("tg") or "tg"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _UNIT_NAME


def runtime_env() -> dict[str, str]:
    """Non-secret state locators the autostart worker must inherit (#34).

    A launchd/systemd worker starts with a scrubbed environment; without
    DATA_DIR it would look for the marker in the default location, miss it
    and disarm itself without ever syncing. Secrets stay in the protected
    data-dir .env — never in world-readable unit files.
    """
    import os

    env = {"DATA_DIR": str(get_data_dir())}
    for key in ("DB_PATH", "TG_SESSION_NAME"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def render_plist(tg_bin: str, log_path: Path, env: dict[str, str] | None = None) -> str:
    from xml.sax.saxutils import escape

    env_items = "".join(
        f"\n        <key>{escape(k)}</key><string>{escape(v)}</string>"
        for k, v in (env or {}).items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{escape(LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(tg_bin)}</string>
        <string>bootstrap</string>
        <string>run</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>{env_items}
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>300</integer>
    <key>StandardOutPath</key><string>{escape(str(log_path))}</string>
    <key>StandardErrorPath</key><string>{escape(str(log_path))}</string>
</dict>
</plist>
"""


def _systemd_quote(value: str) -> str:
    """Quote a value for systemd unit files: %-specifiers doubled,
    backslashes and quotes escaped, whole token double-quoted."""
    value = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def render_systemd_unit(tg_bin: str, env: dict[str, str] | None = None) -> str:
    env_lines = "".join(
        f"Environment={_systemd_quote(f'{k}={v}')}\n" for k, v in (env or {}).items()
    )
    return f"""[Unit]
Description=tg-cli initial sync (self-removing after one full pass)

[Service]
Type=simple
{env_lines}ExecStart={_systemd_quote(tg_bin)} bootstrap run
Restart=on-failure
RestartSec=300

[Install]
WantedBy=default.target
"""


def install_autostart() -> str:
    """Install and kick the platform autostart entry. Returns a description."""
    tg_bin = tg_executable()
    env = runtime_env()
    if sys.platform == "darwin":
        import plistlib

        rendered = render_plist(tg_bin, get_data_dir() / "bootstrap.log", env)
        # Parse before install: a malformed template must fail loudly and
        # leave no partial artifacts behind (#34).
        plistlib.loads(rendered.encode())
        path = launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        try:
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            subprocess.run(
                ["launchctl", "load", "-w", str(path)], capture_output=True, check=True
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return f"LaunchAgent {path}"
    if sys.platform.startswith("linux"):
        rendered = render_systemd_unit(tg_bin, env)
        path = systemd_unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", _UNIT_NAME],
                capture_output=True,
                check=True,
            )
        except BaseException:
            path.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            raise
        return f"systemd user unit {path}"
    raise RuntimeError(
        "Autostart is implemented for macOS (launchd) and Linux (systemd --user). "
        "On Windows schedule `tg bootstrap run` at logon via Task Scheduler."
    )


def uninstall_autostart() -> None:
    """Best-effort removal of the autostart entry on any platform."""
    if sys.platform == "darwin":
        path = launch_agent_path()
        if path.exists():
            # Order matters when the worker uninstalls ITSELF: launchctl
            # terminates the job (this very process), so the plist must be
            # gone before that — otherwise it lingers forever.
            path.unlink(missing_ok=True)
            subprocess.run(["launchctl", "remove", LABEL], capture_output=True)
    elif sys.platform.startswith("linux"):
        path = systemd_unit_path()
        if path.exists():
            # no --now: the service may be uninstalling itself from within
            subprocess.run(["systemctl", "--user", "disable", _UNIT_NAME], capture_output=True)
            path.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def autostart_installed() -> bool:
    if sys.platform == "darwin":
        return launch_agent_path().exists()
    if sys.platform.startswith("linux"):
        return systemd_unit_path().exists()
    return False
