"""Configuration management - loads from .env or environment variables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    """Load configuration with a trust-ordered precedence (#29):

    1. process environment variables (always win — dotenv never overrides),
    2. an explicitly opted-in file from $TG_ENV_FILE,
    3. the trusted data-dir .env.

    A .env lying in the current working directory is deliberately ignored:
    a foreign project's file must not hijack DATA_DIR/DB_PATH/credentials
    of a global CLI that agents run from arbitrary folders.
    """
    if explicit := os.environ.get("TG_ENV_FILE", ""):
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_file():
            load_dotenv(explicit_path)

    if raw_data_dir := os.environ.get("DATA_DIR", ""):
        data_env = Path(raw_data_dir).expanduser() / ".env"
    else:
        data_env = _default_data_home() / "tg-cli" / ".env"
    if data_env.is_file():
        load_dotenv(data_env)


def _default_data_home() -> Path:
    """Return a platform-appropriate base directory for application data."""
    if raw := os.environ.get("XDG_DATA_HOME", ""):
        return Path(raw).expanduser()

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            return Path(local_appdata).expanduser()
        return home / "AppData" / "Local"
    return home / ".local" / "share"


def _resolve_env_path(raw: str) -> Path:
    """Resolve user-provided paths relative to the current working directory."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


_load_env()

APP_NAME = "tg-cli"

# Telegram Desktop built-in credentials (public, no application needed)
_DEFAULT_API_ID = 2040
_DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"


def get_api_id() -> int:
    val = os.environ.get("TG_API_ID", "")
    if val:
        return int(val)
    return _DEFAULT_API_ID


def get_api_hash() -> str:
    val = os.environ.get("TG_API_HASH", "")
    if val:
        return val
    return _DEFAULT_API_HASH


def is_default_api_id() -> bool:
    """Return True if the user has NOT set a custom TG_API_ID."""
    return not os.environ.get("TG_API_ID", "")


def get_session_name() -> str:
    return os.environ.get("TG_SESSION_NAME", "tg_cli")


def get_session_path() -> str:
    """Return session file path inside data/ directory."""
    data_dir = get_data_dir()
    name = get_session_name()
    return str(data_dir / name)


def get_data_dir() -> Path:
    """Return data directory, create if not exists."""
    raw = os.environ.get("DATA_DIR", "")
    if raw:
        d = _resolve_env_path(raw)
    else:
        d = _default_data_home() / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_db_path() -> Path:
    raw = os.environ.get("DB_PATH", "")
    if raw:
        p = _resolve_env_path(raw)
    else:
        p = get_data_dir() / "messages.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
