"""Sandboxed tests for install.sh (#31): stub uv, clean HOME, no network."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer")


def _make_stub_uv(bin_dir: Path, tool_dir: Path) -> None:
    """A fake `uv` that installs a fake `tg` and reports its tool dir."""
    tg_bin_dir = tool_dir / "telegram-to-agent-skill-cli" / "bin"
    stub = f"""#!/bin/bash
case "$1 $2" in
  "tool install")
    mkdir -p "{tg_bin_dir}"
    cat > "{tg_bin_dir}/tg" <<'TG'
#!/bin/bash
if [ "$1" = "--version" ]; then echo "tg, version 0.7.0.dev0"; exit 0; fi
if [ "$1" = "status" ]; then echo "ok: true"; echo "authenticated: false"; exit 0; fi
exit 0
TG
    chmod +x "{tg_bin_dir}/tg"
    echo "Installed 1 executable: tg"
    ;;
  "tool dir") echo "{tool_dir}" ;;
  *) exit 0 ;;
esac
"""
    uv = bin_dir / "uv"
    uv.write_text(stub)
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)


def _run_install(home: Path, extra_env: dict | None = None):
    bin_dir = home / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _make_stub_uv(bin_dir, home / "uv-tools")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(REPO / "install.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )


class TestInstaller:
    def test_clean_home_linux_xdg(self, tmp_path):
        """XDG_DATA_HOME must yield the same data dir the CLI uses (#31)."""
        home = tmp_path / "home"
        home.mkdir()
        xdg = tmp_path / "xdg-data"
        res = _run_install(home, {"XDG_DATA_HOME": str(xdg)})
        assert res.returncode == 0, res.stdout + res.stderr
        env_file = xdg / "tg-cli" / ".env"
        assert env_file.is_file()
        assert (env_file.stat().st_mode & 0o777) == 0o600
        assert ((xdg / "tg-cli").stat().st_mode & 0o777) == 0o700
        # skill chain
        skill = home / ".agents" / "skills" / "tg"
        assert skill.is_symlink()
        assert os.readlink(skill) == str(REPO / "skill")
        claude_link = home / ".claude" / "skills" / "tg"
        assert os.readlink(claude_link) == str(skill)
        assert "tg-skill" in (home / ".claude" / "CLAUDE.md").read_text()

    def test_data_dir_env_override(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        custom = tmp_path / "custom-data"
        res = _run_install(home, {"DATA_DIR": str(custom)})
        assert res.returncode == 0, res.stdout + res.stderr
        assert (custom / ".env").is_file()

    def test_second_run_is_idempotent(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        xdg = tmp_path / "xdg"
        res1 = _run_install(home, {"XDG_DATA_HOME": str(xdg)})
        assert res1.returncode == 0
        env_file = xdg / "tg-cli" / ".env"
        env_file.write_text("TG_API_ID=42\n")
        env_file.chmod(0o644)  # simulate drift
        res2 = _run_install(home, {"XDG_DATA_HOME": str(xdg)})
        assert res2.returncode == 0, res2.stdout + res2.stderr
        assert "already present" in res2.stdout
        assert env_file.read_text() == "TG_API_ID=42\n"  # not clobbered
        assert (env_file.stat().st_mode & 0o777) == 0o600  # perms repaired
        snippet_count = (home / ".claude" / "CLAUDE.md").read_text().count("tg-skill")
        assert snippet_count == 1  # no duplicate snippets

    def test_existing_real_dir_not_clobbered(self, tmp_path):
        """A real directory at the skill path must be an explicit error (#31)."""
        home = tmp_path / "home"
        skill_dir = home / ".agents" / "skills" / "tg"
        skill_dir.mkdir(parents=True)
        (skill_dir / "precious.md").write_text("моё")
        res = _run_install(home, {"XDG_DATA_HOME": str(tmp_path / "x")})
        assert res.returncode != 0
        assert "not a symlink" in res.stdout + res.stderr
        assert (skill_dir / "precious.md").read_text() == "моё"  # intact

    def test_uv_bin_not_in_path(self, tmp_path):
        """Install must succeed via the absolute entrypoint even when the
        uv tool bin is not in PATH (#31)."""
        home = tmp_path / "home"
        home.mkdir()
        res = _run_install(home, {"XDG_DATA_HOME": str(tmp_path / "x")})
        assert res.returncode == 0, res.stdout + res.stderr
        assert "0.7.0" in res.stdout          # version came from $TG_BIN
        assert "not in PATH" in res.stdout    # and the hint was printed
