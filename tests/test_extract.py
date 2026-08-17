"""Tests for links.classify and extract helpers — no network, real TL types."""

from __future__ import annotations

from types import SimpleNamespace

from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageEntityTextUrl,
    MessageEntityUrl,
)

from tg_cli.extract import (
    attachment_meta,
    extract_message_meta,
    extract_urls,
    utf16_slice,
)
from tg_cli.links import classify

# ─────────────────────── links.classify ───────────────────────


class TestClassify:
    def test_google_doc(self):
        kind, fetch = classify("https://docs.google.com/document/d/abc-DEF_123/edit?tab=t.0")
        assert kind == "gdoc"
        assert fetch == "https://docs.google.com/document/d/abc-DEF_123/export?format=txt"

    def test_google_sheet_with_gid(self):
        kind, fetch = classify("https://docs.google.com/spreadsheets/d/sheetID42/edit#gid=777")
        assert kind == "gsheet"
        assert fetch == "https://docs.google.com/spreadsheets/d/sheetID42/export?format=csv&gid=777"

    def test_google_slides(self):
        kind, fetch = classify("https://docs.google.com/presentation/d/slideX/edit")
        assert kind == "gslides"
        assert fetch == "https://docs.google.com/presentation/d/slideX/export/txt"

    def test_tme_link(self):
        kind, fetch = classify("https://t.me/c/1234567/890")
        assert kind == "tme"
        assert fetch is None

    def test_plain_web(self):
        kind, fetch = classify("https://example.com/page?a=1")
        assert kind == "web"
        assert fetch == "https://example.com/page?a=1"


# ─────────────────────── utf16_slice ───────────────────────


class TestUtf16Slice:
    def test_ascii(self):
        assert utf16_slice("hello https://a.io tail", 6, 12) == "https://a.io"

    def test_emoji_before_url(self):
        # 🚀 is one astral char = 2 UTF-16 units; Telegram counts units.
        text = "🚀 https://a.io"
        assert utf16_slice(text, 3, 12) == "https://a.io"


# ─────────────────────── extract_urls ───────────────────────


def _msg(**kw):
    defaults = dict(
        id=1, message="", text=None, entities=None, media=None, reply_to_msg_id=None
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestExtractUrls:
    def test_plain_url_entity(self):
        text = "смотри https://example.com/doc тут"
        msg = _msg(message=text, entities=[MessageEntityUrl(offset=7, length=23)])
        assert extract_urls(msg) == ["https://example.com/doc"]

    def test_text_url_entity(self):
        msg = _msg(
            message="кликни сюда",
            entities=[MessageEntityTextUrl(offset=0, length=6, url="https://hidden.io")],
        )
        assert extract_urls(msg) == ["https://hidden.io"]

    def test_dedup_preserves_order(self):
        text = "https://a.io https://a.io"
        msg = _msg(
            message=text,
            entities=[
                MessageEntityUrl(offset=0, length=12),
                MessageEntityUrl(offset=13, length=12),
            ],
        )
        assert extract_urls(msg) == ["https://a.io"]

    def test_no_entities(self):
        assert extract_urls(_msg(message="просто текст")) == []


# ─────────────────────── attachment_meta ───────────────────────


def _document(mime="application/pdf", size=1024, attributes=None):
    return SimpleNamespace(mime_type=mime, size=size, attributes=attributes or [])


class TestAttachmentMeta:
    def test_pdf_document(self):
        from telethon.tl.types import MessageMediaDocument

        media = MessageMediaDocument.__new__(MessageMediaDocument)
        media.document = _document(
            attributes=[DocumentAttributeFilename(file_name="отчёт.pdf")]
        )
        meta = attachment_meta(media)
        assert meta == {
            "kind": "document",
            "file_name": "отчёт.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
        }

    def test_voice_message(self):
        from telethon.tl.types import MessageMediaDocument

        media = MessageMediaDocument.__new__(MessageMediaDocument)
        media.document = _document(
            mime="audio/ogg",
            attributes=[DocumentAttributeAudio(duration=10, voice=True)],
        )
        assert attachment_meta(media)["kind"] == "voice"

    def test_round_video(self):
        from telethon.tl.types import MessageMediaDocument

        media = MessageMediaDocument.__new__(MessageMediaDocument)
        media.document = _document(
            mime="video/mp4",
            attributes=[DocumentAttributeVideo(duration=10, w=384, h=384, round_message=True)],
        )
        assert attachment_meta(media)["kind"] == "video"

    def test_sticker_is_other(self):
        from telethon.tl.types import InputStickerSetEmpty, MessageMediaDocument

        media = MessageMediaDocument.__new__(MessageMediaDocument)
        media.document = _document(
            mime="image/webp",
            attributes=[
                DocumentAttributeSticker(alt="🙂", stickerset=InputStickerSetEmpty())
            ],
        )
        assert attachment_meta(media)["kind"] == "other"

    def test_image_by_mime(self):
        from telethon.tl.types import MessageMediaDocument

        media = MessageMediaDocument.__new__(MessageMediaDocument)
        media.document = _document(mime="image/png", attributes=[])
        assert attachment_meta(media)["kind"] == "image"

    def test_none_media(self):
        assert attachment_meta(None) is None

    def test_webpage_preview_is_not_attachment(self):
        from telethon.tl.types import MessageMediaWebPage

        media = MessageMediaWebPage.__new__(MessageMediaWebPage)
        assert attachment_meta(media) is None


# ─────────────────────── extract_message_meta ───────────────────────


class TestExtractMessageMeta:
    def test_reply_and_links(self):
        text = "договор тут https://docs.google.com/document/d/xyz/edit"
        msg = _msg(
            message=text,
            reply_to_msg_id=41,
            entities=[MessageEntityUrl(offset=12, length=43)],
        )
        meta = extract_message_meta(msg)
        assert meta["reply_to_msg_id"] == 41
        assert meta["has_media"] is False
        assert meta["attachment"] is None
        assert meta["links"] == [
            {
                "url": "https://docs.google.com/document/d/xyz/edit",
                "fetch_url": "https://docs.google.com/document/d/xyz/export?format=txt",
                "kind": "gdoc",
            }
        ]

    def test_fake_message_without_attrs(self):
        """Duck-typed fakes (as in test_client.py) must not crash extraction."""
        bare = SimpleNamespace(id=1, message="hi", text="hi")
        meta = extract_message_meta(bare)
        assert meta["reply_to_msg_id"] is None
        assert meta["has_media"] is False
        assert meta["links"] == []


class TestStructuralClassify:
    """#41 — hostname/scheme-based classification, no substring tricks."""

    def test_foreign_host_with_google_in_query_stays_web(self):
        url = "https://evil.example/redirect?to=docs.google.com/document/d/x/edit"
        kind, fetch = classify(url)
        assert kind == "web"
        assert fetch == url  # no rewrite to another address

    def test_uppercase_host_and_trailing_dot(self):
        kind, fetch = classify("HTTPS://DOCS.GOOGLE.COM./document/d/abc123/edit")
        assert kind == "gdoc"
        assert fetch == "https://docs.google.com/document/d/abc123/export?format=txt"

    def test_google_u0_path_form(self):
        kind, fetch = classify("https://docs.google.com/spreadsheets/u/0/d/sheet42/edit#gid=7")
        assert kind == "gsheet"
        assert fetch == "https://docs.google.com/spreadsheets/d/sheet42/export?format=csv&gid=7"

    def test_non_http_scheme_not_fetched(self):
        kind, fetch = classify("tg://resolve?domain=somebody")
        assert kind == "web"
        assert fetch is None

    def test_tme_private_and_topic_links(self):
        assert classify("https://t.me/c/1307778786/35896") == ("tme", None)
        assert classify("https://t.me/c/1307778786/12/35896") == ("tme", None)

    def test_tme_public_message_link(self):
        assert classify("https://t.me/durov/123") == ("tme", None)

    def test_tme_profile_and_invite_are_web(self):
        kind, fetch = classify("https://t.me/durov")
        assert (kind, fetch) == ("web", "https://t.me/durov")
        kind, fetch = classify("https://t.me/+AbCdEfGh123")
        assert (kind, fetch) == ("web", "https://t.me/+AbCdEfGh123")


class TestParseTmeMessageLink:
    def test_private(self):
        from tg_cli.links import parse_tme_message_link

        assert parse_tme_message_link("https://t.me/c/1307778786/35896") == (
            -1001307778786,
            35896,
        )

    def test_private_topic_takes_last_number(self):
        from tg_cli.links import parse_tme_message_link

        assert parse_tme_message_link("https://t.me/c/555/12/900") == (
            -1000000000555,
            900,
        )

    def test_public_returns_username(self):
        from tg_cli.links import parse_tme_message_link

        assert parse_tme_message_link("t.me/mychannel/42") == ("mychannel", 42)

    def test_profile_is_none(self):
        from tg_cli.links import parse_tme_message_link

        assert parse_tme_message_link("https://t.me/mychannel") is None
