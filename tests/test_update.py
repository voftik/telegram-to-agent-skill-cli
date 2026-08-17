"""Tests for tg update machinery — no network, injected fetchers."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tg_cli import update as upd
from tg_cli.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestVersionLogic:
    def test_is_newer(self):
        assert upd.is_newer("0.8.0", "0.7.0") is True
        assert upd.is_newer("0.7.0", "0.7.0") is False
        # PEP 440: dev sorts below the release
        assert upd.is_newer("0.7.0", "0.7.0.dev0") is True
        assert upd.is_newer(None, "0.7.0") is False
        assert upd.is_newer("garbage", "0.7.0") is False

    def test_latest_filters_yanked_dev_and_prereleases(self, monkeypatch):
        canned = {
            "releases": {
                "0.7.0": [{"yanked": False}],
                "0.8.0": [{"yanked": True}],          # yanked — excluded
                "0.9.0.dev1": [{"yanked": False}],    # dev — excluded
                "0.9.0rc1": [{"yanked": False}],      # prerelease — excluded
                "not-a-version": [{"yanked": False}],
                "0.7.5": [],                          # no files — excluded
            }
        }

        class FakeResp:
            def read(self):
                return json.dumps(canned).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert upd.fetch_latest() == "0.7.0"

    def test_fetch_offline_returns_none(self, monkeypatch):
        import urllib.request

        def boom(*a, **k):
            raise OSError("no network")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        assert upd.fetch_latest() is None


class TestUpgradeCommand:
    def test_matrix(self):
        assert upd.upgrade_command("uv-tool") == [
            "uv", "tool", "install", "--force", "telegram-to-agent-skill-cli",
        ]
        assert upd.upgrade_command("pipx") == [
            "pipx", "upgrade", "telegram-to-agent-skill-cli",
        ]
        assert upd.upgrade_command("editable") is None
        assert upd.upgrade_command("other") is None


class TestCacheAndStatus:
    def test_cache_roundtrip_and_status(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        upd.write_cache("99.0.0")
        status = upd.update_status()
        assert status["latest"] == "99.0.0"
        assert status["update_available"] is True
        assert status["stale"] is False

    def test_status_stale_without_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        status = upd.update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["stale"] is True

    def test_passive_hint_respects_optout(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        upd.write_cache("99.0.0")
        assert upd.passive_hint() is not None
        monkeypatch.setenv("TG_UPDATE_CHECK", "0")
        assert upd.passive_hint() is None


class TestUpdateCli:
    def test_check_reports_available(self, runner, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(upd, "fetch_latest", lambda timeout=5.0: "99.0.0")
        result = runner.invoke(cli, ["update", "--check", "--yaml"])
        assert result.exit_code == 0
        assert "update_available: true" in result.output
        assert "'99.0.0'" in result.output or "99.0.0" in result.output

    def test_check_offline_is_structured_error(self, runner, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(upd, "fetch_latest", lambda timeout=5.0: None)
        result = runner.invoke(cli, ["update", "--check", "--yaml"])
        assert result.exit_code == 1
        assert "network_unreachable" in result.output

    def test_editable_install_gets_guidance_not_subprocess(
        self, runner, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setattr(upd, "fetch_latest", lambda timeout=5.0: "99.0.0")
        monkeypatch.setattr(upd, "detect_install", lambda: "editable")
        calls = []
        import tg_cli.cli.system as sys_mod

        monkeypatch.setattr(
            sys_mod.subprocess, "run", lambda *a, **k: calls.append(a)
        )
        result = runner.invoke(cli, ["update", "--yes"], env={"OUTPUT": "rich"})
        assert result.exit_code == 1
        assert "Development install" in result.output
        assert calls == []  # no upgrade subprocess for editable installs
