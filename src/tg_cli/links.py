"""URL classification: turn links shared in chats into agent-fetchable URLs.

Agents fetch web pages themselves (WebFetch etc.); this module only decides
*what* to fetch. Google Docs/Sheets/Slides links are rewritten to their
export endpoints — fetching the regular URL returns a JS shell, not content.
"""

from __future__ import annotations

import re

_GDOC = re.compile(r"https?://docs\.google\.com/document/d/([\w-]+)")
_GSHEET = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([\w-]+)")
_GSLIDES = re.compile(r"https?://docs\.google\.com/presentation/d/([\w-]+)")
_GID = re.compile(r"[#?&]gid=(\d+)")
_TME = re.compile(r"https?://t\.me/")


def classify(url: str) -> tuple[str, str | None]:
    """Return (kind, fetch_url) for a shared URL.

    kind: gdoc | gsheet | gslides | tme | web
    fetch_url: what an agent should actually fetch, or None when a plain
    HTTP fetch makes no sense (t.me links resolve via `tg thread` instead).
    """
    if m := _GDOC.search(url):
        return "gdoc", f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
    if m := _GSHEET.search(url):
        fetch = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
        if gid := _GID.search(url):
            fetch += f"&gid={gid.group(1)}"
        return "gsheet", fetch
    if m := _GSLIDES.search(url):
        return "gslides", f"https://docs.google.com/presentation/d/{m.group(1)}/export/txt"
    if _TME.search(url):
        return "tme", None
    return "web", url
