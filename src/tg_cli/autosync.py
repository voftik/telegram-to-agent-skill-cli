"""Scheduled background refresh of the local index.

`tg autosync start` arms a per-user schedule (macOS LaunchAgent with
StartInterval / Linux systemd user timer) that runs `tg autosync run`
every N minutes. Unlike bootstrap this is a standing schedule: it keeps
the index fresh for desktop chat apps served by the read-only MCP
bridge, which never syncs by itself.

The worker steps aside while the bootstrap initial sync is pending
(both would fight over the single Telethon session).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .bootstrap import runtime_env, tg_executable
from .config import get_data_dir

LABEL = "dev.tg-cli.refresh"
_UNIT_BASE = "tg-cli-refresh"
DEFAULT_INTERVAL_MIN = 15
_LOG_MAX_BYTES = 512 * 1024
_LOG_KEEP_BYTES = 64 * 1024


# ─────────────────────── state ───────────────────────


def state_path() -> Path:
    return get_data_dir() / "autosync.json"


def read_state() -> dict | None:
    path = state_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_state(interval_min: int, limit: int, delay: float) -> None:
    state_path().write_text(
        json.dumps({"interval_min": interval_min, "limit": limit, "delay": delay})
    )


def clear_state() -> None:
    state_path().unlink(missing_ok=True)


def log_path() -> Path:
    return get_data_dir() / "refresh.log"


def trim_log() -> None:
    """Keep the append-only log bounded; launchd/systemd never rotate it."""
    path = log_path()
    try:
        if path.is_file() and path.stat().st_size > _LOG_MAX_BYTES:
            data = path.read_bytes()[-_LOG_KEEP_BYTES:]
            path.write_bytes(b"[...trimmed...]\n" + data)
    except OSError:
        pass


# ─────────────────────── platform plumbing ───────────────────────


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def systemd_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{_UNIT_BASE}.service"


def systemd_timer_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{_UNIT_BASE}.timer"


def render_plist(
    tg_bin: str, interval_min: int, log: Path, env: dict[str, str] | None = None
) -> str:
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
        <string>autosync</string>
        <string>run</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>{env_items}
    </dict>
    <key>RunAtLoad</key><true/>
    <key>StartInterval</key><integer>{interval_min * 60}</integer>
    <key>StandardOutPath</key><string>{escape(str(log))}</string>
    <key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict>
</plist>
"""


def render_systemd_service(tg_bin: str, env: dict[str, str] | None = None) -> str:
    from .bootstrap import _systemd_quote

    env_lines = "".join(
        f"Environment={_systemd_quote(f'{k}={v}')}\n" for k, v in (env or {}).items()
    )
    return f"""[Unit]
Description=tg-cli scheduled refresh of the local Telegram index

[Service]
Type=oneshot
{env_lines}ExecStart={_systemd_quote(tg_bin)} autosync run
"""


def render_systemd_timer(interval_min: int) -> str:
    return f"""[Unit]
Description=tg-cli refresh every {interval_min} minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval_min}min
Persistent=true

[Install]
WantedBy=timers.target
"""


def install_schedule(interval_min: int) -> str:
    """Install and start the platform schedule. Returns a description."""
    import subprocess

    tg_bin = tg_executable()
    env = runtime_env()
    if sys.platform == "darwin":
        import plistlib

        rendered = render_plist(tg_bin, interval_min, log_path(), env)
        plistlib.loads(rendered.encode())  # malformed template must fail loudly
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
        return f"LaunchAgent {path} (every {interval_min} min)"
    if sys.platform.startswith("linux"):
        service = systemd_service_path()
        timer = systemd_timer_path()
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text(render_systemd_service(tg_bin, env))
        timer.write_text(render_systemd_timer(interval_min))
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", f"{_UNIT_BASE}.timer"],
                capture_output=True,
                check=True,
            )
        except BaseException:
            service.unlink(missing_ok=True)
            timer.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            raise
        return f"systemd user timer {timer} (every {interval_min} min)"
    raise RuntimeError(
        "Scheduled refresh is implemented for macOS (launchd) and Linux"
        " (systemd --user). On Windows schedule `tg autosync run` via Task"
        " Scheduler, e.g.: schtasks /create /tn tg-refresh /sc minute"
        f" /mo {interval_min} /tr \"tg autosync run\""
    )


def uninstall_schedule() -> None:
    """Best-effort removal of the schedule on any platform."""
    import subprocess

    if sys.platform == "darwin":
        path = launch_agent_path()
        if path.exists():
            path.unlink(missing_ok=True)
            subprocess.run(["launchctl", "remove", LABEL], capture_output=True)
    elif sys.platform.startswith("linux"):
        timer = systemd_timer_path()
        service = systemd_service_path()
        if timer.exists() or service.exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", f"{_UNIT_BASE}.timer"],
                capture_output=True,
            )
            timer.unlink(missing_ok=True)
            service.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def schedule_supported() -> bool:
    """Platforms where install_schedule() can actually arm something."""
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def schedule_installed() -> bool:
    if sys.platform == "darwin":
        return launch_agent_path().exists()
    if sys.platform.startswith("linux"):
        return systemd_timer_path().exists()
    return False
