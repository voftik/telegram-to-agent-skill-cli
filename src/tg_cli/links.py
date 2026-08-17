"""URL classification: turn links shared in chats into agent-fetchable URLs.

Agents fetch web pages themselves (WebFetch etc.); this module only decides
*what* to fetch. Classification is structural (#41): scheme and hostname are
parsed with urllib, never substring-matched — a foreign URL that merely
mentions docs.google.com in its path/query stays a plain web link, and
fetch_url rewrites are produced only for explicitly allowed HTTPS hosts.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_GOOGLE_PATH = re.compile(r"^/(document|spreadsheets|presentation)/(?:u/\d+/)?d/([\w-]+)")
_GID = re.compile(r"(?:^|[#?&])gid=(\d+)")
_TME_HOSTS = {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}

# t.me message paths we can resolve locally via `tg thread`:
#   /c/<internal>/<msg>            private channel message
#   /c/<internal>/<topic>/<msg>    private channel, forum topic
#   /<username>/<msg>              public channel/group message
#   /<username>/<topic>/<msg>      public forum topic message
_TME_PRIVATE_MSG = re.compile(r"^/c/(\d+)(?:/\d+)?/(\d+)/?$")
_TME_PUBLIC_MSG = re.compile(r"^/([A-Za-z]\w{3,31})(?:/\d+)?/(\d+)/?$")


def _hostname(parts) -> str | None:
    host = parts.hostname
    if not host:
        return None
    return host.lower().rstrip(".")


def classify(url: str) -> tuple[str, str | None]:
    """Return (kind, fetch_url) for a shared URL.

    kind: gdoc | gsheet | gslides | tme | web
    fetch_url: what an agent should actually fetch. None when a plain HTTP
    fetch makes no sense (Telegram message links resolve via `tg thread`;
    non-HTTP schemes are preserved but not fetched).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "web", None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        # tg://, ftp:// etc. — preserved as data, never auto-fetched (#41)
        return "web", None
    host = _hostname(parts)
    if host is None:
        return "web", None

    if host == "docs.google.com":
        m = _GOOGLE_PATH.match(parts.path)
        if m:
            doc_type, doc_id = m.group(1), m.group(2)
            if doc_type == "document":
                return "gdoc", f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
            if doc_type == "spreadsheets":
                fetch = f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv"
                gid = _GID.search(parts.query) or _GID.search(parts.fragment)
                if gid:
                    fetch += f"&gid={gid.group(1)}"
                return "gsheet", fetch
            return "gslides", f"https://docs.google.com/presentation/d/{doc_id}/export/txt"
        return "web", url

    if host in _TME_HOSTS:
        if _TME_PRIVATE_MSG.match(parts.path) or _TME_PUBLIC_MSG.match(parts.path):
            # A message link — resolve locally via `tg thread --url`.
            return "tme", None
        # Profiles, invites (+hash), bots, stickers… — ordinary web pages.
        return "web", url

    return "web", url


def parse_tme_message_link(url: str) -> tuple[int | str, int] | None:
    """Parse a t.me message link into (chat, msg_id) for `tg thread` (#41).

    Private links yield the marked channel id (int); public links yield the
    username (str) to resolve against local chat names. Returns None for
    anything that is not a message link.
    """
    try:
        parts = urlsplit(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    if _hostname(parts) not in _TME_HOSTS:
        return None
    if m := _TME_PRIVATE_MSG.match(parts.path):
        return -(1_000_000_000_000 + int(m.group(1))), int(m.group(2))
    if m := _TME_PUBLIC_MSG.match(parts.path):
        return m.group(1), int(m.group(2))
    return None
