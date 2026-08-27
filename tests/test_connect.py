"""Tests for hostapps config writers and the tg connect CLI."""

from __future__ import annotations

import json
import sys

import pytest

from tg_cli import hostapps


@pytest.fixture
def tg_bin(tmp_path):
    p = tmp_path / "bin" / "tg"
    p.parent.mkdir()
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


class TestClaudeDesktopWriter:
    def test_fresh_file_created(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "Claude" / "claude_desktop_config.json"
        cfg_path.parent.mkdir()
        report = hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        assert report["status"] == "added"
        cfg = json.loads(cfg_path.read_text())
        assert cfg["mcpServers"]["tg"]["command"] == str(tg_bin)
        assert cfg["mcpServers"]["tg"]["args"] == ["mcp"]

    def test_merge_preserves_foreign_content(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "claude_desktop_config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "mcpServers": {"other": {"command": "/bin/other"}},
                    "theme": "dark",
                }
            )
        )
        hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        cfg = json.loads(cfg_path.read_text())
        assert cfg["mcpServers"]["other"] == {"command": "/bin/other"}
        assert cfg["theme"] == "dark"
        assert "tg" in cfg["mcpServers"]

    def test_second_run_is_already_and_no_backup(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "claude_desktop_config.json"
        hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        report = hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        assert report["status"] == "already"
        assert not (tmp_path / "claude_desktop_config.json.bak").exists()

    def test_differing_entry_backed_up_and_updated(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "claude_desktop_config.json"
        cfg_path.write_text(
            json.dumps({"mcpServers": {"tg": {"command": "/old/tg", "args": ["mcp"]}}})
        )
        report = hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        assert report["status"] == "updated"
        assert report["backup"]
        bak = json.loads((tmp_path / "claude_desktop_config.json.bak").read_text())
        assert bak["mcpServers"]["tg"]["command"] == "/old/tg"
        cfg = json.loads(cfg_path.read_text())
        assert cfg["mcpServers"]["tg"]["command"] == str(tg_bin)

    def test_invalid_json_refused_untouched(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "claude_desktop_config.json"
        cfg_path.write_text("{broken")
        with pytest.raises(RuntimeError, match="not valid JSON"):
            hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        assert cfg_path.read_text() == "{broken"

    def test_missing_parent_needs_force(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "nope" / "claude_desktop_config.json"
        with pytest.raises(RuntimeError, match="force"):
            hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path)
        report = hostapps.connect_claude_desktop(tg_bin, config_path=cfg_path, force=True)
        assert report["status"] == "added"


class TestCodexWriter:
    def test_append_when_absent(self, tmp_path, tg_bin):
        cfg_path = tmp_path / ".codex" / "config.toml"
        cfg_path.parent.mkdir()
        cfg_path.write_text('model = "o5"\n')
        report = hostapps.connect_codex(tg_bin, config_path=cfg_path)
        assert report["status"] == "added"
        text = cfg_path.read_text()
        assert 'model = "o5"' in text
        assert "[mcp_servers.tg]" in text
        assert 'args = ["mcp"]' in text

    def test_idempotent(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "config.toml"
        cfg_path.parent.mkdir(exist_ok=True)
        cfg_path.write_text("")
        hostapps.connect_codex(tg_bin, config_path=cfg_path)
        first = cfg_path.read_text()
        report = hostapps.connect_codex(tg_bin, config_path=cfg_path)
        assert report["status"] == "already"
        assert cfg_path.read_text() == first

    def test_bounded_rewrite_preserves_other_sections(self, tmp_path, tg_bin):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[mcp_servers.other]\ncommand = \"/bin/other\"\n\n"
            "[mcp_servers.tg]\ncommand = \"/old/tg\"\nargs = [\"mcp\"]\n\n"
            "[profile]\nname = \"x\"\n"
        )
        report = hostapps.connect_codex(tg_bin, config_path=cfg_path)
        assert report["status"] == "updated"
        assert report["backup"]
        text = cfg_path.read_text()
        assert '[mcp_servers.other]' in text
        assert '"/bin/other"' in text
        assert '[profile]' in text
        assert '"/old/tg"' not in text
        assert str(tg_bin) in text

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
    def test_result_parses_as_toml(self, tmp_path, tg_bin):
        import tomllib

        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text('[a]\nb = "c"\n')
        hostapps.connect_codex(tg_bin, config_path=cfg_path)
        parsed = tomllib.loads(cfg_path.read_text())
        assert parsed["mcp_servers"]["tg"]["command"] == str(tg_bin)
        assert parsed["a"]["b"] == "c"


class TestPathsAndDetection:
    def test_tg_binary_path_absolute(self, monkeypatch, tg_bin):
        monkeypatch.setattr(hostapps.shutil, "which", lambda _: str(tg_bin))
        assert hostapps.tg_binary_path() == tg_bin.resolve()

    def test_tg_binary_path_rejects_cache(self, monkeypatch, tmp_path):
        cached = tmp_path / "cache" / "archive-v0" / "tg"
        cached.parent.mkdir(parents=True)
        cached.write_text("")
        monkeypatch.setattr(hostapps.shutil, "which", lambda _: str(cached))
        monkeypatch.setattr(hostapps, "uv_tool_tg_path", lambda: None)
        monkeypatch.setattr(hostapps.sys, "argv", ["/not/tg-named"])
        with pytest.raises(RuntimeError, match="--command"):
            hostapps.tg_binary_path()

    def test_detect_apps_shape(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            hostapps, "claude_config_path", lambda: tmp_path / "Claude" / "cfg.json"
        )
        monkeypatch.setattr(
            hostapps, "codex_config_path", lambda: tmp_path / ".codex" / "config.toml"
        )
        monkeypatch.setattr(hostapps, "perplexity_installed", lambda: False)
        report = hostapps.detect_apps()
        assert set(report) == set(hostapps.APPS)
        for info in report.values():
            assert {"detected", "configured", "broken", "config_path"} <= set(info)
        assert report["claude-desktop"]["detected"] is False

    def test_broken_marks_missing_command(self, monkeypatch, tmp_path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"tg": {"command": "/gone/tg", "args": ["mcp"]}}})
        )
        monkeypatch.setattr(hostapps, "claude_config_path", lambda: cfg)
        monkeypatch.setattr(
            hostapps, "codex_config_path", lambda: tmp_path / "config.toml"
        )
        monkeypatch.setattr(hostapps, "perplexity_installed", lambda: False)
        report = hostapps.detect_apps()
        assert report["claude-desktop"]["configured"] is True
        assert report["claude-desktop"]["broken"] is True


class TestSelftest:
    def test_selftest_against_real_bridge(self):
        # The venv's own tg entry point runs the actual server.
        import shutil

        tg = shutil.which("tg")
        assert tg, "tg entry point must exist in the test environment"
        result = hostapps.bridge_selftest(tg)
        assert result["ok"], result
        assert result["tools"] == 6

    def test_selftest_reports_broken_binary(self, tmp_path):
        bogus = tmp_path / "tg"
        bogus.write_text("#!/bin/sh\nexit 3\n")
        bogus.chmod(0o755)
        result = hostapps.bridge_selftest(bogus)
        assert result["ok"] is False
        assert "error" in result


class TestConnectCli:
    def test_status_json_shape(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from tg_cli.cli.main import cli

        monkeypatch.setattr(
            hostapps, "claude_config_path", lambda: tmp_path / "Claude" / "cfg.json"
        )
        monkeypatch.setattr(
            hostapps, "codex_config_path", lambda: tmp_path / ".codex" / "config.toml"
        )
        monkeypatch.setattr(hostapps, "perplexity_installed", lambda: False)
        result = CliRunner().invoke(cli, ["connect", "status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload["data"]) == set(hostapps.APPS)

    def test_wizard_apps_none_skips(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from tg_cli import skillpkg
        from tg_cli.cli.main import cli

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(skillpkg.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.delenv("TG_API_HASH", raising=False)
        (home / ".claude").mkdir()
        result = CliRunner().invoke(
            cli,
            [
                "setup", "--yes", "--skip-login", "--skip-bootstrap",
                "--agents", "none", "--apps", "none",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Desktop apps" not in result.output

    def test_wizard_apps_unknown_rejected(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from tg_cli import skillpkg
        from tg_cli.cli.main import cli

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(skillpkg.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        result = CliRunner().invoke(
            cli,
            [
                "setup", "--yes", "--skip-login", "--skip-bootstrap",
                "--agents", "none", "--apps", "winamp",
            ],
        )
        assert result.exit_code == 1
        assert "unknown app" in result.output
