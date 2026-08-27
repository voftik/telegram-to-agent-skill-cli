"""Wire the tg MCP bridge into desktop chat apps (pure logic, no click).

Claude Desktop and Codex/ChatGPT desktop take a config-file entry we can
write ourselves; Perplexity only accepts connectors through its own UI,
so for it we produce a copy-paste snippet. GUI apps launch MCP servers
with a scrubbed PATH, so every written config uses an absolute path to
the tg binary.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

APPS = ("claude-desktop", "codex", "perplexity")

MCP_ARGS = ["mcp"]
_CODEX_HEADER = "[mcp_servers.tg]"
_CODEX_COMMENT = "# tg Telegram context (added by tg connect)"


# ─────────────────────── tg binary resolution ───────────────────────


def uv_tool_tg_path() -> Path | None:
    """$(uv tool dir)/<package>/bin/tg — stable across `tg update`."""
    try:
        out = subprocess.run(
            ["uv", "tool", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    candidate = Path(out) / "telegram-to-agent-skill-cli" / "bin" / "tg"
    return candidate if candidate.exists() else None


def _is_transient(path: Path) -> bool:
    """uvx runs from an ephemeral cache — never wire a GUI app to it."""
    parts = {p.lower() for p in path.parts}
    return "cache" in parts or any(p.startswith("archive-") for p in path.parts)


def tg_binary_path() -> Path:
    """Absolute path to a durable tg entry point, for GUI app configs."""
    which = shutil.which("tg")
    if which:
        p = Path(which).resolve()
        if p.exists() and not _is_transient(p):
            return p
    stable = uv_tool_tg_path()
    if stable is not None:
        return stable
    candidate = Path(sys.argv[0]).resolve()
    if candidate.name in ("tg", "tg.exe") and candidate.exists():
        return candidate
    raise RuntimeError(
        "Could not locate the tg binary; pass --command with an absolute path"
    )


# ─────────────────────── config locations ───────────────────────


def claude_config_path() -> Path:
    import os

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / (
            "claude_desktop_config.json"
        )
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def perplexity_installed() -> bool:
    return sys.platform == "darwin" and Path("/Applications/Perplexity.app").exists()


# ─────────────────────── snippets ───────────────────────


def snippet_json(tg_path: Path) -> str:
    """Perplexity's Advanced connector tab and any mcpServers-style host."""
    return json.dumps(
        {"command": str(tg_path), "args": MCP_ARGS}, indent=2, ensure_ascii=False
    )


def _codex_section_lines(tg_path: Path) -> list[str]:
    return [
        _CODEX_COMMENT,
        _CODEX_HEADER,
        f"command = {json.dumps(str(tg_path))}",
        f"args = {json.dumps(MCP_ARGS)}",
    ]


def snippet_toml(tg_path: Path) -> str:
    return "\n".join(_codex_section_lines(tg_path)[1:])


# ─────────────────────── writers ───────────────────────


def _backup(path: Path) -> str:
    bak = path.with_name(path.name + ".bak")
    shutil.copy2(path, bak)
    return str(bak)


def connect_claude_desktop(
    tg_path: Path, config_path: Path | None = None, force: bool = False
) -> dict:
    """Merge {"mcpServers": {"tg": ...}} into claude_desktop_config.json."""
    path = config_path or claude_config_path()
    entry = {"command": str(tg_path), "args": MCP_ARGS}

    if not path.parent.is_dir() and not force:
        raise RuntimeError(
            f"{path.parent} does not exist — is Claude Desktop installed?"
            " Rerun with --force to create it anyway."
        )

    cfg: dict = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RuntimeError(
                f"{path} is not valid JSON ({e}); fix or remove it first —"
                " refusing to touch a file I cannot parse."
            ) from None
        if not isinstance(cfg, dict):
            raise RuntimeError(f"{path} does not contain a JSON object")

    servers = cfg.setdefault("mcpServers", {})
    if servers.get("tg") == entry:
        return {"app": "claude-desktop", "status": "already", "config_path": str(path)}

    status = "updated" if "tg" in servers else "added"
    backup = _backup(path) if path.is_file() else None
    servers["tg"] = entry

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {
        "app": "claude-desktop",
        "status": status,
        "config_path": str(path),
        "backup": backup,
    }


def connect_codex(tg_path: Path, config_path: Path | None = None) -> dict:
    """Append or rewrite the [mcp_servers.tg] section in ~/.codex/config.toml.

    ChatGPT desktop shares this file with Codex CLI, so one entry covers
    both. Rewrites touch only the bounded section (header line up to the
    next [section] or EOF), with a .bak first and a parse check after.
    """
    path = config_path or codex_config_path()
    if not path.parent.is_dir():
        raise RuntimeError(
            f"{path.parent} does not exist — is Codex CLI or the ChatGPT"
            " desktop app installed?"
        )

    section = _codex_section_lines(tg_path)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = existing.splitlines()

    start = next(
        (i for i, ln in enumerate(lines) if ln.strip() == _CODEX_HEADER), None
    )
    if start is not None:
        end = next(
            (
                j
                for j in range(start + 1, len(lines))
                if lines[j].lstrip().startswith("[")
            ),
            len(lines),
        )
        current = [ln for ln in lines[start:end] if ln.strip() and not ln.startswith("#")]
        wanted = [ln for ln in section if not ln.startswith("#")]
        if current == wanted:
            return {"app": "codex", "status": "already", "config_path": str(path)}
        backup = _backup(path)
        # Rewrite only the bounded section; section[1:] skips the comment
        # (already present above the header from the original append).
        body = "\n".join(lines[:start] + section[1:] + lines[end:])
        status = "updated"
    else:
        backup = _backup(path) if path.is_file() else None
        prefix = existing.rstrip("\n")
        block = "\n".join(section)
        body = (prefix + "\n\n" + block + "\n") if prefix else block + "\n"
        status = "added"

    _verify_toml(body, path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"app": "codex", "status": status, "config_path": str(path), "backup": backup}


def _verify_toml(body: str, path: Path, backup: str | None) -> None:
    """Parse-check the candidate config on Python 3.11+ (tomllib is stdlib)."""
    try:
        import tomllib
    except ImportError:  # Python 3.10 — skip verification, never a dependency
        return
    try:
        parsed = tomllib.loads(body)
    except tomllib.TOMLDecodeError as e:
        hint = f" (previous content saved at {backup})" if backup else ""
        raise RuntimeError(
            f"Refusing to write {path}: the result would not parse as TOML"
            f" ({e}){hint}"
        ) from None
    server = parsed.get("mcp_servers", {}).get("tg", {})
    if server.get("args") != MCP_ARGS:
        raise RuntimeError(f"Refusing to write {path}: tg section landed wrong")


# ─────────────────────── detection / status ───────────────────────


def _configured_command(app: str) -> str | None:
    """The command currently wired for tg in the app's config, if any."""
    if app == "claude-desktop":
        path = claude_config_path()
        if not path.is_file():
            return None
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            return cfg.get("mcpServers", {}).get("tg", {}).get("command")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return None
    if app == "codex":
        path = codex_config_path()
        if not path.is_file():
            return None
        try:
            import tomllib

            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            return cfg.get("mcp_servers", {}).get("tg", {}).get("command")
        except Exception:  # noqa: BLE001 - tomllib missing on 3.10 or bad file
            # Fallback: cheap textual probe, command unknown.
            text = path.read_text(encoding="utf-8", errors="replace")
            return "?" if _CODEX_HEADER in text else None
    return None


def detect_apps() -> dict[str, dict]:
    """{app: {detected, configured, broken, config_path}} for the status table."""
    report: dict[str, dict] = {}

    claude_dir = claude_config_path().parent
    codex_dir = codex_config_path().parent
    detection = {
        "claude-desktop": claude_dir.is_dir(),
        "codex": codex_dir.is_dir(),
        "perplexity": perplexity_installed(),
    }
    config_paths = {
        "claude-desktop": str(claude_config_path()),
        "codex": str(codex_config_path()),
        "perplexity": None,
    }
    for app in APPS:
        command = _configured_command(app)
        configured = command is not None
        broken = bool(
            command and command != "?" and not Path(command).exists()
        )
        report[app] = {
            "detected": detection[app],
            "configured": configured,
            "broken": broken,
            "config_path": config_paths[app],
        }
    return report


# ─────────────────────── bridge self-test ───────────────────────


def bridge_selftest(tg_path: Path, timeout: float = 30.0) -> dict:
    """Spawn the exact command the configs point at and do a real MCP
    handshake — surfaces a broken path before the user opens the app."""
    request = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize",'
        '"params":{"protocolVersion":"2025-06-18"}}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    try:
        proc = subprocess.run(
            [str(tg_path), *MCP_ARGS],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    try:
        init = json.loads(lines[0])
        tools = json.loads(lines[1])
        return {
            "ok": True,
            "tools": len(tools["result"]["tools"]),
            "server_version": init["result"]["serverInfo"]["version"],
        }
    except (IndexError, KeyError, json.JSONDecodeError):
        err = proc.stderr.strip().splitlines()
        return {
            "ok": False,
            "error": "unexpected bridge output"
            + (f": {err[-1]}" if err else f" (exit {proc.returncode})"),
        }
