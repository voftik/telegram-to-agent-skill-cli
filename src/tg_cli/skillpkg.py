"""Agent-skill installation from the packaged copy.

The canonical skill ships inside the wheel (``tg_cli/skill``). Installation
copies it to ``~/.agents/skills/tg`` (a real directory — venv internals are
not stable across upgrades) and symlinks ``~/.claude/skills/tg`` to it, the
same chain install.sh used to build. A manifest with per-file hashes makes
re-installs idempotent, detects user modifications, and enables a clean
uninstall.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from importlib import metadata, resources
from pathlib import Path

MARKER = "<!-- tg-skill -->"
MANIFEST_NAME = ".tg-skill-manifest.json"

# Snippet bodies are frozen byte-for-byte: they are marker-guarded and
# append-only on user machines. Evolvable guidance belongs in SKILL.md.
CLAUDE_SNIPPET = f"""
{MARKER}
## Telegram-контекст (скилл tg)
Когда пользователь упоминает Telegram-чаты, каналы или переписку («что обсуждали в чатике X», «коллеги скинули», «найди в телеграме», «саммари канала», «ответь в чат») — используй скилл tg (~/.claude/skills/tg) и CLI `tg`. Read-only по умолчанию; запись в Telegram (send/edit/delete) только с `--confirm` после явного «да» пользователя в текущей сессии. Не применяй скилл к задачам разработки Telegram-ботов (Bot API).
"""

CODEX_SNIPPET = f"""
{MARKER}
## Telegram context (tg CLI)
The user's Telegram account is synced locally by the `tg` CLI (installed via uv). When the user mentions Telegram chats, channels or correspondence ("что обсуждали в чатике", "коллеги скинули", "найди в телеграме", "саммари канала", "ответь в чат") — use it. Read the full playbook first: run `cat ~/.agents/skills/tg/SKILL.md` (scenarios live in the references/ subfolder next to it).
Hard rules: read-only by default; every Telegram write (send/edit/delete) needs the user's explicit "yes" in the current session and the `--confirm` flag (without it all three are dry-runs). Default reading depth: 7 days or 200 messages — run `tg brief CHAT` before going deeper. All commands support --yaml. Not for Telegram Bot API development tasks.
"""


def package_version() -> str:
    try:
        return metadata.version("telegram-to-agent-skill-cli")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def packaged_skill_root():
    """The skill directory shipped inside the package (all install modes)."""
    return resources.files("tg_cli") / "skill"


def agents_skill_dir() -> Path:
    return Path.home() / ".agents" / "skills" / "tg"


def claude_skill_link() -> Path:
    return Path.home() / ".claude" / "skills" / "tg"


def _iter_skill_files(root) -> list[tuple[str, bytes]]:
    """(relative_path, content) pairs of the packaged skill, sorted."""
    out: list[tuple[str, bytes]] = []

    def walk(node, prefix: str) -> None:
        for child in node.iterdir():
            rel = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, rel + "/")
            else:
                out.append((rel, child.read_bytes()))

    walk(root, "")
    return sorted(out)


def _manifest_for(files: list[tuple[str, bytes]]) -> dict:
    return {
        "version": package_version(),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in files},
    }


def read_manifest(target: Path) -> dict | None:
    path = target / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _dir_matches_manifest(target: Path, manifest: dict) -> bool:
    for rel, digest in manifest.get("files", {}).items():
        f = target / rel
        if not f.is_file():
            return False
        if hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            return False
    return True


def install_skill(*, force: bool = False, dev_source: Path | None = None) -> dict:
    """Install the skill for agents. Returns a report dict.

    dev_source: symlink to a checkout's skill dir instead of copying —
    the development mode (changes flow through without reinstalling).
    """
    target = agents_skill_dir()
    target.parent.mkdir(parents=True, exist_ok=True)

    if dev_source is not None:
        if target.exists() and not target.is_symlink():
            if not force:
                raise RuntimeError(
                    f"{target} is a real directory; re-run with --force to replace"
                )
            _backup(target)
        tmp_link = target.parent / f".{target.name}.new"
        tmp_link.unlink(missing_ok=True)
        tmp_link.symlink_to(dev_source)
        tmp_link.replace(target)
        _link_claude(target)
        return {"mode": "dev-symlink", "target": str(target), "source": str(dev_source)}

    files = _iter_skill_files(packaged_skill_root())
    manifest = _manifest_for(files)

    if target.is_symlink():
        # Old install layout (or dev mode) — a link holds no user content,
        # replacing it with a real copy is always safe.
        target.unlink()
    elif target.exists():
        existing = read_manifest(target)
        if existing is None or not _dir_matches_manifest(target, existing):
            if not force:
                raise RuntimeError(
                    f"{target} exists with unmanaged or modified content; "
                    "re-run with --force to back it up and replace"
                )
            _backup(target)
        else:
            shutil.rmtree(target)

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for rel, data in files:
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (target / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    _link_claude(target)
    return {
        "mode": "copy",
        "target": str(target),
        "version": manifest["version"],
        "files": len(files),
    }


def _backup(target: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = target.parent / f"{target.name}.backup-{stamp}"
    target.rename(backup)
    return backup


def _link_claude(target: Path) -> None:
    link = claude_skill_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # No symlink privileges by default — keep a second copy in sync.
        if link.exists() and not link.is_symlink():
            shutil.rmtree(link)
        shutil.copytree(target, link, dirs_exist_ok=True)
        return
    if link.exists() or link.is_symlink():
        if link.is_symlink():
            link.unlink()
        else:
            raise RuntimeError(f"{link} is a real directory; move it away first")
    link.symlink_to(target)


def uninstall_skill() -> dict:
    """Remove the managed skill install (manifest-guarded)."""
    target = agents_skill_dir()
    removed = []
    link = claude_skill_link()
    if link.is_symlink():
        link.unlink()
        removed.append(str(link))
    if target.is_symlink():
        target.unlink()
        removed.append(str(target))
    elif target.exists():
        if read_manifest(target) is None:
            raise RuntimeError(f"{target} has no manifest — refusing to delete")
        shutil.rmtree(target)
        removed.append(str(target))
    return {"removed": removed}


def skill_status() -> dict:
    target = agents_skill_dir()
    if target.is_symlink():
        return {
            "installed": True,
            "mode": "dev-symlink",
            "target": str(target),
            "source": str(Path(target).resolve()),
            "stale": False,
        }
    if not target.exists():
        return {"installed": False}
    manifest = read_manifest(target)
    if manifest is None:
        return {"installed": True, "mode": "unmanaged", "target": str(target)}
    current = package_version()
    return {
        "installed": True,
        "mode": "copy",
        "target": str(target),
        "version": manifest.get("version"),
        "stale": manifest.get("version") != current,
        "modified": not _dir_matches_manifest(target, manifest),
    }


def append_snippets(agents: set[str]) -> dict:
    """Append marker-guarded auto-activation snippets. Idempotent."""
    report: dict[str, str] = {}
    if "claude" in agents:
        report["claude"] = _append_once(
            Path.home() / ".claude" / "CLAUDE.md", CLAUDE_SNIPPET
        )
    if "codex" in agents:
        codex_dir = Path.home() / ".codex"
        if codex_dir.is_dir():
            report["codex"] = _append_once(codex_dir / "AGENTS.md", CODEX_SNIPPET)
        else:
            report["codex"] = "skipped (no ~/.codex)"
    return report


def _append_once(path: Path, snippet: str) -> str:
    try:
        existing = path.read_text()
    except FileNotFoundError:
        existing = ""
    if MARKER in existing:
        return "already present"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(snippet)
    return "appended"
