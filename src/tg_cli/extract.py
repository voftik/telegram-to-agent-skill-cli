"""Extract structured metadata from Telethon messages at sync time.

Upstream stores only text; this module pulls out what the fork needs:
reply threading, attachment metadata (files are downloaded lazily later)
and shared links. All accessors are defensive (getattr) so test fakes and
service messages pass through safely.
"""

from __future__ import annotations

from typing import Any

from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from .links import classify


def utf16_slice(text: str, offset: int, length: int) -> str:
    """Slice text by UTF-16 code units — Telegram entity offsets use them."""
    data = text.encode("utf-16-le")
    return data[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")


def _document_meta(document: Any) -> tuple[str, str | None, str | None, int | None]:
    """Classify a Telethon Document into (kind, file_name, mime_type, size)."""
    mime = getattr(document, "mime_type", None)
    size = getattr(document, "size", None)
    file_name = None
    kind = "document"

    is_sticker = False
    is_animated = False
    is_voice = False
    is_round = False
    is_audio = False
    is_video = False
    for attr in getattr(document, "attributes", None) or []:
        if isinstance(attr, DocumentAttributeFilename):
            file_name = attr.file_name
        elif isinstance(attr, DocumentAttributeSticker):
            is_sticker = True
        elif isinstance(attr, DocumentAttributeAnimated):
            is_animated = True
        elif isinstance(attr, DocumentAttributeAudio):
            is_voice = bool(getattr(attr, "voice", False))
            is_audio = not is_voice
        elif isinstance(attr, DocumentAttributeVideo):
            is_round = bool(getattr(attr, "round_message", False))
            is_video = True

    if is_sticker or is_animated:
        kind = "other"
    elif is_voice:
        kind = "voice"
    elif is_round or is_video:
        kind = "video"
    elif is_audio:
        kind = "audio"
    elif mime and mime.startswith("image/"):
        kind = "image"
    return kind, file_name, mime, size


def _photo_meta(photo: Any) -> tuple[str, str | None, str | None, int | None]:
    size = None
    for s in getattr(photo, "sizes", None) or []:
        byte_count = getattr(s, "size", None)
        if isinstance(byte_count, int):
            size = max(size or 0, byte_count)
    return "image", None, "image/jpeg", size


def attachment_meta(media: Any) -> dict[str, Any] | None:
    """Return attachment metadata for real media, None for web previews."""
    if media is None or isinstance(media, MessageMediaWebPage):
        return None
    if isinstance(media, MessageMediaPhoto):
        kind, file_name, mime, size = _photo_meta(getattr(media, "photo", None))
    elif isinstance(media, MessageMediaDocument):
        kind, file_name, mime, size = _document_meta(getattr(media, "document", None))
    else:
        # Polls, geo, contacts, dice… — nothing to download.
        return None
    return {
        "kind": kind,
        "file_name": file_name,
        "mime_type": mime,
        "size_bytes": size,
    }


def extract_urls(msg: Any) -> list[str]:
    """Collect URLs from message entities and web-page previews, in order."""
    urls: list[str] = []
    text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
    for ent in getattr(msg, "entities", None) or []:
        if isinstance(ent, MessageEntityTextUrl):
            urls.append(ent.url)
        elif isinstance(ent, MessageEntityUrl):
            url = utf16_slice(text, ent.offset, ent.length)
            if url:
                urls.append(url)
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaWebPage):
        page_url = getattr(getattr(media, "webpage", None), "url", None)
        if page_url:
            urls.append(page_url)
    seen: set[str] = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def extract_message_meta(msg: Any) -> dict[str, Any]:
    """Everything the fork stores beyond text, from one Telethon message."""
    media = getattr(msg, "media", None)
    attachment = attachment_meta(media)
    links = []
    for url in extract_urls(msg):
        kind, fetch_url = classify(url)
        links.append({"url": url, "fetch_url": fetch_url, "kind": kind})
    return {
        "reply_to_msg_id": getattr(msg, "reply_to_msg_id", None),
        "has_media": attachment is not None,
        "attachment": attachment,
        "links": links,
    }
