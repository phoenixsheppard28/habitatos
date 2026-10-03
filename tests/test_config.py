from habitat import config
from habitat.db import database_url


def test_data_dir_from_env_file_takes_effect(tmp_path, monkeypatch):
    monkeypatch.delenv(config.DATA_DIR_VARIABLE, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"HABITAT_DATA_DIR={tmp_path / 'archive'}\nMOVEBANK_USERNAME=someone\n")

    loaded = config.load_settings(env_file)

    assert loaded.data_dir == (tmp_path / "archive").resolve()
    assert loaded.raw_dir == loaded.data_dir / "raw"
    assert loaded.movebank_credentials is None


def test_process_environment_wins_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("HABITAT_DATA_DIR=/from/file\n")
    monkeypatch.setenv(config.DATA_DIR_VARIABLE, str(tmp_path))

    assert config.load_settings(env_file).data_dir == tmp_path.resolve()


def test_relative_data_dir_is_relative_to_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_VARIABLE, "elsewhere")

    assert config.load_settings(tmp_path / "missing.env").data_dir == config.PROJECT_ROOT / "elsewhere"


def test_override_after_import_takes_effect(tmp_path):
    config.configure(data_dir=tmp_path)

    assert config.settings().raw_dir == tmp_path / "raw"


def test_missing_database_url_raises(monkeypatch):
    config.configure(database_url=None)

    try:
        database_url()
    except RuntimeError as error:
        assert config.DATABASE_URL_VARIABLE in str(error)
    else:
        raise AssertionError("expected RuntimeError")
