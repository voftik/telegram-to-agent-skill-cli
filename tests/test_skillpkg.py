"""Tests for skillpkg — packaged-skill installation with manifest."""

from __future__ import annotations

import json

import pytest

from tg_cli import skillpkg


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(skillpkg.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


class TestInstall:
    def test_fresh_install_copies_and_links(self, home):
        report = skillpkg.install_skill()
        assert report["mode"] == "copy"
        target = home / ".agents" / "skills" / "tg"
        assert (target / "SKILL.md").is_file()
        assert (target / "references" / "analyze-chat.md").is_file()
        manifest = json.loads((target / skillpkg.MANIFEST_NAME).read_text())
        assert "SKILL.md" in manifest["files"]
        link = home / ".claude" / "skills" / "tg"
        assert link.is_symlink()
        assert link.resolve() == target.resolve()

    def test_reinstall_is_idempotent(self, home):
        skillpkg.install_skill()
        report = skillpkg.install_skill()  # must not raise: manifest matches
        assert report["mode"] == "copy"

    def test_old_symlink_layout_migrates_silently(self, home, tmp_path):
        target = home / ".agents" / "skills" / "tg"
        target.parent.mkdir(parents=True)
        elsewhere = tmp_path / "somewhere"
        elsewhere.mkdir()
        target.symlink_to(elsewhere)
        report = skillpkg.install_skill()
        assert report["mode"] == "copy"
        assert not target.is_symlink()
        assert (target / "SKILL.md").is_file()

    def test_foreign_directory_refused_without_force(self, home):
        target = home / ".agents" / "skills" / "tg"
        target.mkdir(parents=True)
        (target / "precious.md").write_text("моё")
        with pytest.raises(RuntimeError, match="unmanaged"):
            skillpkg.install_skill()
        assert (target / "precious.md").read_text() == "моё"

    def test_force_backs_up_foreign_directory(self, home):
        target = home / ".agents" / "skills" / "tg"
        target.mkdir(parents=True)
        (target / "precious.md").write_text("моё")
        skillpkg.install_skill(force=True)
        backups = list(target.parent.glob("tg.backup-*"))
        assert len(backups) == 1
        assert (backups[0] / "precious.md").read_text() == "моё"
        assert (target / "SKILL.md").is_file()

    def test_user_modified_copy_refused_without_force(self, home):
        skillpkg.install_skill()
        target = home / ".agents" / "skills" / "tg"
        (target / "SKILL.md").write_text("правил руками")
        with pytest.raises(RuntimeError, match="modified"):
            skillpkg.install_skill()

    def test_dev_symlink_mode(self, home, tmp_path):
        src = tmp_path / "checkout-skill"
        src.mkdir()
        (src / "SKILL.md").write_text("dev")
        report = skillpkg.install_skill(dev_source=src)
        assert report["mode"] == "dev-symlink"
        target = home / ".agents" / "skills" / "tg"
        assert target.is_symlink()
        assert (target / "SKILL.md").read_text() == "dev"


class TestStatusAndUninstall:
    def test_status_fresh(self, home):
        assert skillpkg.skill_status() == {"installed": False}
        skillpkg.install_skill()
        st = skillpkg.skill_status()
        assert st["installed"] is True
        assert st["mode"] == "copy"
        assert st["modified"] is False

    def test_status_detects_modification(self, home):
        skillpkg.install_skill()
        (home / ".agents" / "skills" / "tg" / "SKILL.md").write_text("x")
        assert skillpkg.skill_status()["modified"] is True

    def test_uninstall_managed_copy(self, home):
        skillpkg.install_skill()
        report = skillpkg.uninstall_skill()
        assert len(report["removed"]) == 2
        assert skillpkg.skill_status() == {"installed": False}

    def test_uninstall_refuses_unmanaged(self, home):
        target = home / ".agents" / "skills" / "tg"
        target.mkdir(parents=True)
        (target / "x.md").write_text("y")
        with pytest.raises(RuntimeError, match="no manifest"):
            skillpkg.uninstall_skill()


class TestSnippets:
    def test_append_and_idempotency(self, home):
        report = skillpkg.append_snippets({"claude"})
        assert report["claude"] == "appended"
        content = (home / ".claude" / "CLAUDE.md").read_text()
        assert content.count(skillpkg.MARKER) == 1
        report2 = skillpkg.append_snippets({"claude"})
        assert report2["claude"] == "already present"
        assert (home / ".claude" / "CLAUDE.md").read_text().count(skillpkg.MARKER) == 1

    def test_codex_gated_on_directory(self, home):
        report = skillpkg.append_snippets({"codex"})
        assert report["codex"] == "skipped (no ~/.codex)"
        (home / ".codex").mkdir()
        report2 = skillpkg.append_snippets({"codex"})
        assert report2["codex"] == "appended"
        assert skillpkg.MARKER in (home / ".codex" / "AGENTS.md").read_text()
