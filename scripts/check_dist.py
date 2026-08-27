#!/usr/bin/env python3
"""Verify built artifacts honour the packaging contract (#33).

sdist: full source snapshot — canonical skill with references, installer,
docs, bilingual README. Also: no stale-upstream references (kabi-tg-cli),
and every relative link in skill/SKILL.md resolves inside the archive.
The wheel ships runtime code only; the supported skill delivery is a
repo clone (or unpacked sdist) + install.sh — see docs/INSTALL.md.
"""

from __future__ import annotations

import re
import sys
import tarfile
from pathlib import Path

_SKILL_FILES = [
    "SKILL.md",
    "references/analyze-chat.md",
    "references/digest.md",
    "references/reply-as-me.md",
]

REQUIRED_SDIST = [
    *[f"src/tg_cli/skill/{f}" for f in _SKILL_FILES],
    "docs/INSTALL.md",
    "install.sh",
    "README.md",
    "README.ru.md",
    "SCHEMA.md",
    "src/tg_cli/cli/main.py",
]

# The wheel is the canonical delivered artifact: it MUST carry the skill
# so `tg skill install` works for plain PyPI installs (no clone).
REQUIRED_WHEEL = [f"tg_cli/skill/{f}" for f in _SKILL_FILES]

FORBIDDEN_PATTERNS = {
    "kabi-tg-cli": "stale upstream package reference",
    "uv tool install kabi": "stale upstream install instruction",
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    dist = Path("dist")
    sdists = sorted(dist.glob("*.tar.gz"))
    if not sdists:
        fail("no sdist in dist/")
    with tarfile.open(sdists[-1]) as tf:
        names = tf.getnames()
        root = names[0].split("/")[0]
        members = {n.removeprefix(root + "/") for n in names}

        for required in REQUIRED_SDIST:
            if required not in members:
                fail(f"sdist misses {required}")

        sdist_skill = {
            name.removeprefix("src/tg_cli/skill/"): tf.extractfile(f"{root}/{name}").read()
            for name in members
            if name.startswith("src/tg_cli/skill/") and not name.endswith("/")
        }

    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        fail("no wheel in dist/")
    import zipfile

    with zipfile.ZipFile(wheels[-1]) as zf:
        wheel_names = set(zf.namelist())
        for required in REQUIRED_WHEEL:
            if required not in wheel_names:
                fail(f"wheel misses {required}")

        wheel_skill = {
            name.removeprefix("tg_cli/skill/"): zf.read(name)
            for name in wheel_names
            if name.startswith("tg_cli/skill/")
        }
        for name, data in wheel_skill.items():
            if not name.endswith(".md"):
                continue
            text = data.decode("utf-8")
            for pattern, why in FORBIDDEN_PATTERNS.items():
                if pattern in text:
                    fail(f"wheel skill {name}: {why} ({pattern!r})")

        skill_md = wheel_skill["SKILL.md"].decode("utf-8")
        for link in re.findall(r"\]\(([^)#]+)\)", skill_md):
            if link.startswith(("http://", "https://")):
                continue
            if link not in wheel_skill:
                fail(f"SKILL.md links to file missing from the wheel: {link}")

        # sdist and wheel must ship byte-identical skills
        for name, data in wheel_skill.items():
            if sdist_skill.get(name) != data:
                fail(f"skill file differs between sdist and wheel: {name}")

    # The Claude Code plugin layout (skills/tg) must stay a byte-identical
    # copy of the packaged skill — marketplace installs pin to git commits.
    plugin_skill = Path("skills/tg")
    packaged = Path("src/tg_cli/skill")
    if plugin_skill.is_dir():
        for f in packaged.rglob("*"):
            if f.is_dir():
                continue
            rel = f.relative_to(packaged)
            twin = plugin_skill / rel
            if not twin.is_file():
                fail(
                    f"plugin skill misses {rel} — resync: "
                    "rm -rf skills/tg && cp -R src/tg_cli/skill skills/tg"
                )
            if twin.read_bytes() != f.read_bytes():
                fail(f"plugin skill differs at {rel} — resync skills/tg from src/tg_cli/skill")
    else:
        fail("skills/tg plugin copy missing")

    # Plugin metadata must carry the SAME version as pyproject — the release
    # guard only checks pyproject, so drift here would ship silently.
    try:
        import tomllib
    except ImportError:  # Python 3.10 — CI (3.12) still enforces this
        tomllib = None
    if tomllib is not None:
        import json

        pyproject_version = tomllib.load(open("pyproject.toml", "rb"))["project"][
            "version"
        ]
        plugin = json.load(open(".claude-plugin/plugin.json"))
        marketplace = json.load(open(".claude-plugin/marketplace.json"))
        if plugin.get("version") != pyproject_version:
            fail(
                f"plugin.json version {plugin.get('version')} != pyproject"
                f" {pyproject_version}"
            )
        for entry in marketplace.get("plugins", []):
            if entry.get("version") != pyproject_version:
                fail(
                    f"marketplace.json plugin version {entry.get('version')} !="
                    f" pyproject {pyproject_version}"
                )

    print(f"OK: {sdists[-1].name} and {wheels[-1].name} honour the contract")


if __name__ == "__main__":
    main()
