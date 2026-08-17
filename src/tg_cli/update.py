"""Self-update machinery for `tg update` and the passive version hint.

Design rules (validated in the distribution plan):
- the hot path NEVER performs network I/O — hints read a cache file only;
- the cache refreshes inside commands that are already network-bound
  (`tg status`, `tg update`, `tg setup`);
- version comparison is PEP 440 via `packaging`, never hand-rolled;
- the npm launcher and `tg update` install UNPINNED so future updates
  keep working.
"""

from __future__ import annotations

import json
import os
import sys
import time
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

from .config import get_data_dir, harden_path

PACKAGE = "telegram-to-agent-skill-cli"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
RELEASES_URL = "https://github.com/voftik/telegram-to-agent-skill-cli/releases/tag"
CACHE_TTL = 24 * 3600


def current_version() -> str:
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "0.0.0"


# ─────────────────────── install-kind detection ───────────────────────


def detect_install() -> str:
    """uv-tool | pipx | editable | other — how this CLI was installed."""
    try:
        direct = metadata.distribution(PACKAGE).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return "other"
    if direct:
        try:
            info = json.loads(direct)
        except ValueError:
            info = {}
        if info.get("dir_info", {}).get("editable"):
            return "editable"
    prefix = sys.prefix
    if f"{os.sep}uv{os.sep}tools{os.sep}" in prefix:
        return "uv-tool"
    if f"{os.sep}pipx{os.sep}venvs{os.sep}" in prefix:
        return "pipx"
    return "other"


def editable_checkout() -> str | None:
    """The clone path behind an editable install, for migration guidance."""
    try:
        direct = metadata.distribution(PACKAGE).read_text("direct_url.json")
        info = json.loads(direct or "{}")
        url = info.get("url", "")
        if url.startswith("file://"):
            return url.removeprefix("file://")
    except (metadata.PackageNotFoundError, ValueError):
        pass
    return None


def upgrade_command(kind: str) -> list[str] | None:
    """The subprocess to run for this install kind, or None when the user
    must act manually (guidance is printed instead)."""
    if kind == "uv-tool":
        # --force also heals a pinned requirement recorded at install time.
        return ["uv", "tool", "install", "--force", PACKAGE]
    if kind == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    return None


# ─────────────────────── latest-version lookup ───────────────────────


def fetch_latest(timeout: float = 5.0) -> str | None:
    """Newest stable, non-yanked version on PyPI, or None when offline."""
    import urllib.request

    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    best: Version | None = None
    for raw, files in (data.get("releases") or {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease:
            continue
        if not files or all(f.get("yanked") for f in files):
            continue
        if best is None or version > best:
            best = version
    return str(best) if best else None


def is_newer(latest: str | None, current: str) -> bool:
    if not latest:
        return False
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


# ─────────────────────── check cache ───────────────────────


def cache_path() -> Path:
    return get_data_dir() / "update-check.json"


def read_cache() -> dict | None:
    try:
        return json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return None


def write_cache(latest: str | None) -> None:
    payload = {
        "latest": latest,
        "checked_at": int(time.time()),
        "current_at_check": current_version(),
    }
    path = cache_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        harden_path(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def refresh_cache(fetch=None) -> dict:
    """Fetch latest (network!) and persist; returns the status block."""
    latest = (fetch or fetch_latest)()  # resolved at call time — patchable
    if latest is not None:
        write_cache(latest)
    return update_status(refresh=False)


def update_status(refresh: bool = False, fetch=None) -> dict:
    """The `update:` block for tg status payloads. Cache-only by default."""
    if refresh:
        return refresh_cache(fetch)
    cache = read_cache() or {}
    latest = cache.get("latest")
    checked_at = cache.get("checked_at")
    return {
        "current": current_version(),
        "latest": latest,
        "update_available": is_newer(latest, current_version()),
        "checked_at": checked_at,
        "stale": checked_at is None or (time.time() - checked_at) > CACHE_TTL,
    }


def passive_hint() -> str | None:
    """One-line stderr hint, cache-only, or None. Never touches the network."""
    if os.environ.get("TG_UPDATE_CHECK", "1") == "0":
        return None
    status = update_status()
    if status["update_available"] and not status["stale"]:
        return (
            f"tg {status['latest']} is available (installed {status['current']})"
            f" — run `tg update`"
        )
    return None
