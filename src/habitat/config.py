import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

DATABASE_URL_VARIABLE = "HABITAT_DATABASE_URL"
DATA_DIR_VARIABLE = "HABITAT_DATA_DIR"


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    data_dir: Path
    anthropic_api_key: str | None
    movebank_username: str | None
    movebank_password: str | None
    classifier_url: str | None = None
    classifier_threshold: float = 0.7

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def movebank_credentials(self) -> tuple[str, str] | None:
        if self.movebank_username and self.movebank_password:
            return self.movebank_username, self.movebank_password
        return None


def load_settings(env_file: Path = ENV_FILE) -> Settings:
    """Values in the process environment win over values in the .env file."""
    values = {**dotenv_values(env_file), **os.environ} if env_file.is_file() else dict(os.environ)
    data_dir = Path(values.get(DATA_DIR_VARIABLE) or PROJECT_ROOT / "data").expanduser()
    classifier_threshold = float(values.get("HABITAT_CLASSIFIER_THRESHOLD") or "0.7")
    if not 0 <= classifier_threshold <= 1:
        raise ValueError("HABITAT_CLASSIFIER_THRESHOLD must be between 0 and 1.")

    return Settings(
        database_url=values.get(DATABASE_URL_VARIABLE) or None,
        data_dir=(data_dir if data_dir.is_absolute() else PROJECT_ROOT / data_dir).resolve(),
        anthropic_api_key=values.get("ANTHROPIC_API_KEY") or None,
        movebank_username=values.get("MOVEBANK_USERNAME") or None,
        movebank_password=values.get("MOVEBANK_PASSWORD") or None,
        classifier_url=values.get("HABITAT_CLASSIFIER_URL") or None,
        classifier_threshold=classifier_threshold,
    )


_current: Settings | None = None


def settings() -> Settings:
    global _current
    if _current is None:
        _current = load_settings()
    return _current


def configure(**changes) -> Settings:
    """Replace some settings for this process, for example `configure(data_dir=tmp_path)` in a test."""
    global _current
    _current = replace(settings(), **changes)
    return _current


def reset() -> None:
    global _current
    _current = None
