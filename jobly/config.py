"""Loading config.yaml / profile.yaml from the Jobly home folder."""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .models import Profile

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def home_dir() -> Path:
    return Path(os.environ.get("JOBLY_HOME", Path.home() / ".jobly")).expanduser()


def _interpolate(value: Any) -> Any:
    """Replace ${VAR} / ${VAR:-default} with environment values, recursively."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


class ScoutConfig(BaseModel):
    enabled: bool = True
    default_interval_minutes: float = 10
    max_workers: int = 4
    # On the very first scan of a source, only alert for jobs posted this recently
    # (everything else is stored quietly so you are not flooded).
    first_run_alert_hours: float = 24


class SearchConfig(BaseModel):
    titles_include: list[str] = Field(default_factory=list)
    titles_exclude: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    max_age_days: float = 30
    min_score: int = 0


class EmailNotify(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    to: str = ""


class NotifyConfig(BaseModel):
    min_score: int = 40
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    webhook_url: str = ""
    email: EmailNotify = Field(default_factory=EmailNotify)


class AIConfig(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str = "high"  # low | medium | high | xhigh | max
    # Generate the full application kit automatically for new jobs scoring at
    # least this much (0 = only when you click "Generate").
    auto_tailor_min_score: int = 0
    max_parallel_kits: int = 2


class Config(BaseModel):
    scout: ScoutConfig = Field(default_factory=ScoutConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    sources: list[dict[str, Any]] = Field(default_factory=list)


class Paths:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else home_dir()

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    @property
    def profile(self) -> Path:
        return self.root / "profile.yaml"

    @property
    def db(self) -> Path:
        return self.root / "jobly.db"

    @property
    def kits(self) -> Path:
        return self.root / "kits"


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(_interpolate(data))


def load_profile(path: Path) -> Profile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


def profile_to_yaml(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def example_file(name: str) -> str:
    return resources.files("jobly").joinpath("data", name).read_text(encoding="utf-8")


def init_home(paths: Paths, overwrite: bool = False) -> list[Path]:
    """Create the home folder with example config.yaml and profile.yaml."""
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.kits.mkdir(exist_ok=True)
    created = []
    for name, target in (("config.example.yaml", paths.config), ("profile.example.yaml", paths.profile)):
        if overwrite or not target.exists():
            target.write_text(example_file(name), encoding="utf-8")
            created.append(target)
    return created

