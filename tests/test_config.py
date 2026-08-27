"""Tests for config module."""


class TestConfig:
    def test_get_api_id(self, monkeypatch):
        monkeypatch.setenv("TG_API_ID", "12345")
        from tg_cli.config import get_api_id

        assert get_api_id() == 12345

    def test_get_api_id_default(self, monkeypatch):
        monkeypatch.delenv("TG_API_ID", raising=False)
        from tg_cli.config import get_api_id

        assert get_api_id() == 2040

    def test_get_api_hash(self, monkeypatch):
        monkeypatch.setenv("TG_API_HASH", "abc123")
        from tg_cli.config import get_api_hash

        assert get_api_hash() == "abc123"

    def test_get_api_hash_default(self, monkeypatch):
        monkeypatch.delenv("TG_API_HASH", raising=False)
        from tg_cli.config import get_api_hash

        assert get_api_hash() == "b18441a1ff607e10a989891a5462e627"

    def test_get_session_name_default(self, monkeypatch):
        monkeypatch.delenv("TG_SESSION_NAME", raising=False)
        from tg_cli.config import get_session_name

        assert get_session_name() == "tg_cli"

    def test_get_session_name_custom(self, monkeypatch):
        monkeypatch.setenv("TG_SESSION_NAME", "my_session")
        from tg_cli.config import get_session_name

        assert get_session_name() == "my_session"

    def test_get_db_path_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DB_PATH", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        import tg_cli.config as cfg

        path = cfg.get_db_path()
        assert path.name == "messages.db"
        assert path.parent.exists()
        assert path.parent == tmp_path / "xdg" / "tg-cli"

    def test_get_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        import tg_cli.config as cfg

        d = cfg.get_data_dir()
        assert d.exists()
        assert d == tmp_path / "xdg" / "tg-cli"

    def test_load_env_from_data_dir(self, monkeypatch, tmp_path):
        """Installed CLI must pick up <data_dir>/.env from any cwd."""
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        data_dir = tmp_path / "xdg" / "tg-cli"
        data_dir.mkdir(parents=True)
        (data_dir / ".env").write_text("TG_API_ID=777\n")
        monkeypatch.chdir(tmp_path)  # cwd without .env
        import tg_cli.config as cfg

        cfg._load_env()
        assert cfg.get_api_id() == 777

    def test_foreign_cwd_env_is_ignored(self, monkeypatch, tmp_path):
        """A project-local .env must not hijack the global CLI (#29)."""
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.delenv("TG_ENV_FILE", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        data_dir = tmp_path / "xdg" / "tg-cli"
        data_dir.mkdir(parents=True)
        (data_dir / ".env").write_text("TG_API_ID=777\n")
        cwd = tmp_path / "proj"
        cwd.mkdir()
        (cwd / ".env").write_text("TG_API_ID=555\nDATA_DIR=./stolen\n")
        monkeypatch.chdir(cwd)
        import tg_cli.config as cfg

        cfg._load_env()
        assert cfg.get_api_id() == 777
        assert "DATA_DIR" not in __import__("os").environ or \
            __import__("os").environ["DATA_DIR"] != "./stolen"

    def test_explicit_env_file_opt_in(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        custom = tmp_path / "custom.env"
        custom.write_text("TG_API_ID=444\n")
        monkeypatch.setenv("TG_ENV_FILE", str(custom))
        import tg_cli.config as cfg

        cfg._load_env()
        assert cfg.get_api_id() == 444

    def test_process_env_beats_env_files(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.setenv("TG_API_ID", "999")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        data_dir = tmp_path / "xdg" / "tg-cli"
        data_dir.mkdir(parents=True)
        (data_dir / ".env").write_text("TG_API_ID=777\n")
        import tg_cli.config as cfg

        cfg._load_env()
        assert cfg.get_api_id() == 999

    def test_get_data_dir_from_env_relative_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATA_DIR", "./runtime-data")
        import tg_cli.config as cfg

        d = cfg.get_data_dir()
        assert d == tmp_path / "runtime-data"

    def test_get_db_path_from_env_relative_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DB_PATH", "./runtime/messages.db")
        import tg_cli.config as cfg

        path = cfg.get_db_path()
        assert path == tmp_path / "runtime" / "messages.db"


class TestApiCredentialPair:
    def test_both_set_returns_custom_pair(self, monkeypatch):
        monkeypatch.setenv("TG_API_ID", "777")
        monkeypatch.setenv("TG_API_HASH", "d" * 32)
        from tg_cli.config import get_api_credentials

        assert get_api_credentials() == (777, "d" * 32)

    def test_neither_set_returns_builtin_pair(self, monkeypatch):
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.delenv("TG_API_HASH", raising=False)
        from tg_cli.config import get_api_credentials

        assert get_api_credentials() == (2040, "b18441a1ff607e10a989891a5462e627")

    def test_lone_variable_raises(self, monkeypatch):
        import pytest

        from tg_cli.config import get_api_credentials

        monkeypatch.setenv("TG_API_ID", "777")
        monkeypatch.delenv("TG_API_HASH", raising=False)
        with pytest.raises(RuntimeError, match="together"):
            get_api_credentials()

        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.setenv("TG_API_HASH", "d" * 32)
        with pytest.raises(RuntimeError, match="together"):
            get_api_credentials()
