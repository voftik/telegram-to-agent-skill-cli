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

REQUIRED_SDIST = [
    "skill/SKILL.md",
    "skill/references/analyze-chat.md",
    "skill/references/digest.md",
    "skill/references/reply-as-me.md",
    "docs/INSTALL.md",
    "install.sh",
    "README.md",
    "README.ru.md",
    "SCHEMA.md",
    "src/tg_cli/cli/main.py",
]

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

        for name in members:
            if not (name.startswith("skill/") and name.endswith(".md")):
                continue
            data = tf.extractfile(f"{root}/{name}").read().decode("utf-8")
            for pattern, why in FORBIDDEN_PATTERNS.items():
                if pattern in data:
                    fail(f"{name}: {why} ({pattern!r})")

        skill_md = tf.extractfile(f"{root}/skill/SKILL.md").read().decode("utf-8")
        for link in re.findall(r"\]\(([^)#]+)\)", skill_md):
            if link.startswith(("http://", "https://")):
                continue
            if f"skill/{link}" not in members:
                fail(f"skill/SKILL.md links to missing file: {link}")

    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        fail("no wheel in dist/")

    print(f"OK: {sdists[-1].name} and {wheels[-1].name} honour the contract")


if __name__ == "__main__":
    main()
