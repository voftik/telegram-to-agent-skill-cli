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


def render_plist(tg_bin: str, log_path: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{tg_bin}</string>
        <string>bootstrap</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>300</integer>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""


def render_systemd_unit(tg_bin: str) -> str:
    return f"""[Unit]
Description=tg-cli initial sync (self-removing after one full pass)

[Service]
Type=simple
ExecStart={tg_bin} bootstrap run
Restart=on-failure
RestartSec=300

[Install]
WantedBy=default.target
"""


def install_autostart() -> str:
    """Install and kick the platform autostart entry. Returns a description."""
    tg_bin = tg_executable()
    if sys.platform == "darwin":
        path = launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_plist(tg_bin, get_data_dir() / "bootstrap.log"))
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, check=True)
        return f"LaunchAgent {path}"
    if sys.platform.startswith("linux"):
        path = systemd_unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_systemd_unit(tg_bin))
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", _UNIT_NAME],
            capture_output=True,
            check=True,
        )
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
            subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
            path.unlink(missing_ok=True)
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
