"""Tests for the tg setup wizard (non-interactive paths)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from tg_cli.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def wizard_home(monkeypatch, tmp_path):
    from tg_cli import skillpkg

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(skillpkg.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    (home / ".claude").mkdir()
    return home, tmp_path / "data"


class TestSetupWizard:
    def test_yes_mode_writes_env_and_installs_skill(self, runner, wizard_home):
        home, data = wizard_home
        result = runner.invoke(
            cli,
            [
                "setup", "--yes", "--skip-login", "--skip-bootstrap",
                "--api-id", "12345",
                "--api-hash", "a" * 32,
                "--agents", "claude",
            ],
        )
        assert result.exit_code == 0, result.output
        env = (data / ".env").read_text()
        assert "TG_API_ID=12345" in env
        assert ((data / ".env").stat().st_mode & 0o777) == 0o600
        assert (home / ".agents" / "skills" / "tg" / "SKILL.md").is_file()
        assert "tg-skill" in (home / ".claude" / "CLAUDE.md").read_text()

    def test_yes_without_creds_fails_loudly(self, runner, wizard_home):
        result = runner.invoke(
            cli, ["setup", "--yes", "--skip-login", "--skip-bootstrap"]
        )
        assert result.exit_code == 1
        assert "--api-id" in result.output

    def test_existing_env_not_clobbered(self, runner, wizard_home):
        home, data = wizard_home
        data.mkdir(parents=True, exist_ok=True)
        (data / ".env").write_text("TG_API_ID=999\nTG_API_HASH=fff\n")
        result = runner.invoke(
            cli,
            ["setup", "--yes", "--skip-login", "--skip-bootstrap", "--agents", "none"],
        )
        assert result.exit_code == 0, result.output
        assert (data / ".env").read_text() == "TG_API_ID=999\nTG_API_HASH=fff\n"

    def test_bad_api_hash_rejected(self, runner, wizard_home):
        result = runner.invoke(
            cli,
            [
                "setup", "--yes", "--skip-login", "--skip-bootstrap",
                "--api-id", "1", "--api-hash", "not-hex",
            ],
        )
        assert result.exit_code == 1
        assert "32" in result.output
