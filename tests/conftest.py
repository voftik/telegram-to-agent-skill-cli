"""Shared test fixtures."""

import os
from datetime import datetime, timedelta, timezone

import pytest

# Set env vars before importing
os.environ.setdefault("TG_API_ID", "0")
os.environ.setdefault("TG_API_HASH", "test")
os.environ.setdefault("OUTPUT", "rich")

from tg_cli.db import MessageDB


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    """Never let tests touch the user's real data dir (sent.log, files/…)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "tg-data"))


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    d = MessageDB(db_path=db_path)
    yield d
    d.close()


@pytest.fixture
def populated_db(tmp_path, monkeypatch):
    """Create a temp DB with sample data and patch config to use it."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    import tg_cli.config as config_mod

    monkeypatch.setattr(config_mod, "_PROJECT_ROOT", tmp_path)

    db = MessageDB(db_path=db_path)

    now = datetime.now(timezone.utc)
    messages = [
        dict(
            chat_id=100,
            chat_name="TestGroup",
            msg_id=i,
            sender_id=42,
            sender_name="Alice",
            content=f"Message {i}: {'Web3' if i % 2 == 0 else 'Python'} discussion",
            timestamp=now - timedelta(hours=i),
        )
        for i in range(1, 11)
    ]
    db.insert_batch(messages)
    yield db, db_path
    db.close()


def make_msg(
    chat_id: int = 100,
    chat_name: str = "TestChat",
    msg_id: int = 1,
    sender_id: int = 42,
    sender_name: str = "Alice",
    content: str = "Hello World",
    hours_ago: float = 0,
    reply_to_msg_id: int | None = None,
    has_media: bool = False,
):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dict(
        chat_id=chat_id,
        chat_name=chat_name,
        msg_id=msg_id,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        timestamp=ts,
        reply_to_msg_id=reply_to_msg_id,
        has_media=has_media,
    )
