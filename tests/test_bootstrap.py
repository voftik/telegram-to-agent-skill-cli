"""Tests for the reboot-resilient bootstrap sync."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from tg_cli import bootstrap as bs
from tg_cli.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()

# ─────────────────────── marker ───────────────────────


class TestMarker:
    def test_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        assert bs.read_marker() is None
        bs.write_marker(delay=3.5, limit=1000)
        assert bs.read_marker() == {"delay": 3.5, "limit": 1000}
        bs.clear_marker()
        assert bs.read_marker() is None

    def test_corrupt_marker_reads_as_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        bs.marker_path().write_text("{broken")
        assert bs.read_marker() == {}


# ─────────────────────── templates ───────────────────────


class TestTemplates:
    def test_plist_content(self):
        text = bs.render_plist("/usr/local/bin/tg", Path("/tmp/b.log"))
        assert f"<string>{bs.LABEL}</string>" in text
        assert "<string>/usr/local/bin/tg</string>" in text
        assert "<string>bootstrap</string>" in text
        assert "<string>run</string>" in text
        assert "<key>RunAtLoad</key><true/>" in text
        assert "<key>SuccessfulExit</key><false/>" in text
        assert "<integer>300</integer>" in text

    def test_systemd_unit_content(self):
        text = bs.render_systemd_unit("/home/u/.local/bin/tg")
        assert 'ExecStart="/home/u/.local/bin/tg" bootstrap run' in text
        assert "Restart=on-failure" in text
        assert "RestartSec=300" in text
        assert "WantedBy=default.target" in text

    def test_plist_parses_with_hostile_paths(self):
        """Parser-based check: spaces, &, %, quotes and Unicode (#34)."""
        import plistlib

        hostile = '/tmp/A&B <x> "квоты" 100%/tg'
        env = {"DATA_DIR": '/tmp/данные & "прочее" 50%'}
        data = plistlib.loads(
            bs.render_plist(hostile, Path("/tmp/л&g.log"), env).encode()
        )
        assert data["ProgramArguments"][0] == hostile
        assert data["EnvironmentVariables"]["DATA_DIR"] == env["DATA_DIR"]

    def test_systemd_unit_quotes_hostile_values(self):
        hostile = '/opt/tg with space/&весёлый%/tg'
        env = {"DATA_DIR": '/data dir/"q"/100%'}
        text = bs.render_systemd_unit(hostile, env)
        # systemd unquoting: strip quotes, %% -> %, \" -> "
        exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
        quoted = exec_line[len("ExecStart="):].rsplit(" bootstrap run", 1)[0]
        assert quoted.startswith('"') and quoted.endswith('"')
        unquoted = quoted[1:-1].replace("%%", "%").replace('\\"', '"')
        assert unquoted == hostile
        env_line = next(line for line in text.splitlines() if line.startswith("Environment="))
        env_val = env_line[len("Environment="):]
        assert env_val.startswith('"') and env_val.endswith('"')
        restored = env_val[1:-1].replace("%%", "%").replace('\\"', '"')
        assert restored == f"DATA_DIR={env['DATA_DIR']}"

    def test_runtime_env_carries_state_locators(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "custom"))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "m.db"))
        monkeypatch.setenv("TG_SESSION_NAME", "alt")
        env = bs.runtime_env()
        assert env["DATA_DIR"] == str(tmp_path / "custom")
        assert env["DB_PATH"] == str(tmp_path / "m.db")
        assert env["TG_SESSION_NAME"] == "alt"
        # no secrets ever
        assert "TG_API_ID" not in env
        assert "TG_API_HASH" not in env


# ─────────────────────── worker semantics ───────────────────────


class TestRun:
    def test_run_without_marker_exits_clean_and_cleans_up(
        self, runner, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        removed = []
        monkeypatch.setattr(bs, "uninstall_autostart", lambda: removed.append(True))
        result = runner.invoke(cli, ["bootstrap", "run"])
        assert result.exit_code == 0
        assert removed == [True]

    def test_run_with_marker_success_disarms(self, runner, monkeypatch, tmp_path):
        from contextlib import asynccontextmanager

        import tg_cli.cli.tg as tg_mod

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "m.db"))
        bs.write_marker(delay=0, limit=10)

        @asynccontextmanager
        async def fake_connect():
            yield object()

        async def fake_sync_all(client, db, limit_per_chat, delay):
            assert limit_per_chat == 10
            assert delay == 0
            return {
                "enumerated": True,
                "error": None,
                "total": 1,
                "ok": 1,
                "partial": 0,
                "failed": 0,
                "new_messages": 5,
                "results": {1: {"name": "chat", "new": 5, "status": "complete", "error": None}},
            }

        import tg_cli.client as client_mod

        monkeypatch.setattr(tg_mod, "connect", fake_connect, raising=False)
        monkeypatch.setattr(client_mod, "connect", fake_connect)
        monkeypatch.setattr(client_mod, "sync_all", fake_sync_all)
        removed = []
        monkeypatch.setattr(bs, "uninstall_autostart", lambda: removed.append(True))

        result = runner.invoke(cli, ["bootstrap", "run"])
        assert result.exit_code == 0
        assert bs.read_marker() is None
        assert removed == [True]

    def test_run_failure_keeps_marker(self, runner, monkeypatch, tmp_path):
        from contextlib import asynccontextmanager

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "m.db"))
        bs.write_marker(delay=0, limit=10)

        @asynccontextmanager
        async def broken_connect():
            raise RuntimeError("database is locked")
            yield  # pragma: no cover

        import tg_cli.client as client_mod

        monkeypatch.setattr(client_mod, "connect", broken_connect)
        result = runner.invoke(cli, ["bootstrap", "run"])
        assert result.exit_code == 1
        assert bs.read_marker() is not None  # still pending — will retry

    def test_run_incomplete_pass_keeps_marker(self, runner, monkeypatch, tmp_path):
        """A pass with failed chats or unhealed gaps must NOT disarm (#19)."""
        from contextlib import asynccontextmanager

        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "m.db"))
        bs.write_marker(delay=0, limit=10)

        @asynccontextmanager
        async def fake_connect():
            yield object()

        async def failing_sync_all(client, db, limit_per_chat, delay):
            return {
                "enumerated": True,
                "error": None,
                "total": 3,
                "ok": 2,
                "partial": 0,
                "failed": 1,
                "new_messages": 7,
                "results": {},
            }

        import tg_cli.client as client_mod

        monkeypatch.setattr(client_mod, "connect", fake_connect)
        monkeypatch.setattr(client_mod, "sync_all", failing_sync_all)
        removed = []
        monkeypatch.setattr(bs, "uninstall_autostart", lambda: removed.append(True))

        result = runner.invoke(cli, ["bootstrap", "run"])
        assert result.exit_code == 1
        assert bs.read_marker() is not None
        assert removed == []
