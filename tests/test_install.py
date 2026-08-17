"""Sandboxed tests for the reduced developer install.sh (#31, phase B)."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer")


def _make_stub_uv(bin_dir: Path, tool_dir: Path) -> Path:
    """Fake uv that installs a fake tg recording its argv."""
    tg_bin_dir = tool_dir / "telegram-to-agent-skill-cli" / "bin"
    log = tool_dir / "tg-args.log"
    stub = f"""#!/bin/bash
case "$1 $2" in
  "tool install")
    mkdir -p "{tg_bin_dir}"
    cat > "{tg_bin_dir}/tg" <<'TG'
#!/bin/bash
echo "$@" >> "{log}"
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
    return log


class TestInstaller:
    def test_delegates_to_tg_setup(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        bin_dir = home / "bin"
        bin_dir.mkdir()
        log = _make_stub_uv(bin_dir, home / "uv-tools")
        res = subprocess.run(
            ["bash", str(REPO / "install.sh"), "--yes", "--skip-login"],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"},
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert log.read_text().strip() == "setup --yes --skip-login"

    def test_missing_uv_is_explicit_error(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        res = subprocess.run(
            ["bash", str(REPO / "install.sh")],
            capture_output=True,
            text=True,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        )
        assert res.returncode == 1
        assert "uv is required" in res.stderr
