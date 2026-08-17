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

    print(f"OK: {sdists[-1].name} and {wheels[-1].name} honour the contract")


if __name__ == "__main__":
    main()
