"""Tests for the scheduled refresh (tg autosync) — no launchctl/systemctl."""

from __future__ import annotations

import plistlib

from click.testing import CliRunner

from tg_cli import autosync
from tg_cli.cli.main import cli


class TestRendering:
    def test_plist_parses_and_carries_interval(self, tmp_path):
        rendered = autosync.render_plist(
            "/usr/local/bin/tg", 15, tmp_path / "refresh.log", {"DATA_DIR": "/d"}
        )
        parsed = plistlib.loads(rendered.encode())
        assert parsed["Label"] == "dev.tg-cli.refresh"
        assert parsed["StartInterval"] == 900
        assert parsed["ProgramArguments"] == ["/usr/local/bin/tg", "autosync", "run"]
        assert parsed["EnvironmentVariables"] == {"DATA_DIR": "/d"}

    def test_units_never_leak_secrets(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TG_API_ID", "123")
        monkeypatch.setenv("TG_API_HASH", "e" * 32)
        from tg_cli.bootstrap import runtime_env

        env = runtime_env()
        plist = autosync.render_plist("/bin/tg", 15, tmp_path / "l.log", env)
        service = autosync.render_systemd_service("/bin/tg", env)
        for rendered in (plist, service):
            assert "TG_API_ID" not in rendered
            assert "e" * 32 not in rendered

    def test_timer_interval(self):
        timer = autosync.render_systemd_timer(30)
        assert "OnUnitActiveSec=30min" in timer
        assert "Persistent=true" in timer

    def test_systemd_service_is_oneshot(self):
        service = autosync.render_systemd_service("/bin/tg")
        assert "Type=oneshot" in service
        assert "autosync run" in service


class TestState:
    def test_state_roundtrip(self):
        autosync.write_state(20, 1000, 0.5)
        state = autosync.read_state()
        assert state == {"interval_min": 20, "limit": 1000, "delay": 0.5}
        autosync.clear_state()
        assert autosync.read_state() is None

    def test_trim_log_bounds_size(self):
        path = autosync.log_path()
        path.write_bytes(b"x" * (600 * 1024))
        autosync.trim_log()
        assert path.stat().st_size < 100 * 1024
        assert path.read_bytes().startswith(b"[...trimmed...]")

    def test_trim_log_leaves_small_file(self):
        path = autosync.log_path()
        path.write_bytes(b"small")
        autosync.trim_log()
        assert path.read_bytes() == b"small"


class TestWorker:
    def test_run_skips_while_bootstrap_pending(self):
        from tg_cli import bootstrap as bs

        bs.write_marker(2.0, 5000)
        try:
            result = CliRunner().invoke(cli, ["autosync", "run"])
            assert result.exit_code == 0, result.output
            assert "bootstrap" in result.output
        finally:
            bs.clear_marker()

    def test_run_fails_when_enumeration_fails(self, monkeypatch):
        from tg_cli.cli import tg as tg_cli_mod

        async def _bad_pass(**kwargs):
            return {"enumerated": False, "error": "boom", "total": 0}

        monkeypatch.setattr(tg_cli_mod, "sync_all_dialogs", _bad_pass)
        result = CliRunner().invoke(cli, ["autosync", "run"])
        assert result.exit_code == 1
        assert "incomplete" in result.output

    def test_status_not_armed(self, monkeypatch):
        monkeypatch.setattr(autosync, "schedule_installed", lambda: False)
        result = CliRunner().invoke(cli, ["autosync", "status", "--json"])
        assert result.exit_code == 0, result.output
        import json

        payload = json.loads(result.output)["data"]
        assert payload["armed"] is False
        assert "messages_indexed" in payload

    def test_stop_without_schedule_is_clean(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(autosync, "uninstall_schedule", lambda: calls.append("un"))
        result = CliRunner().invoke(cli, ["autosync", "stop"])
        assert result.exit_code == 0, result.output
        assert calls == ["un"]
