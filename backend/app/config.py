from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Job Application Assistant"
    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{PROJECT_DIR / 'data' / 'job_assistant.db'}"
    app_base_url: str = "http://127.0.0.1:8000"
    frontend_origin: str = "http://localhost:5173"
    auto_submit: bool = False
    browser_headless: bool = False
    browser_slow_mo: int = Field(default=0, ge=0)
    browser_profile_dir: Path = PROJECT_DIR / "data" / "browser-profile"
    resume_dir: Path = PROJECT_DIR / "data" / "resumes"
    screenshot_dir: Path = PROJECT_DIR / "data" / "screenshots"
    log_dir: Path = PROJECT_DIR / "data" / "logs"
    ai_provider: Literal["codex", "openai", "auto", "none"] = "codex"
    codex_home_dir: Path = PROJECT_DIR / "data" / "codex"
    codex_workspace_dir: Path = PROJECT_DIR / "data" / "codex-workspace"
    codex_bin: Path | None = None
    codex_model: str | None = None
    codex_effort: Literal["low", "medium", "high"] = "low"
    openai_api_key: str | None = None
    openai_model: str | None = None
    log_level: str = "INFO"
    approval_ttl_minutes: int = Field(default=15, ge=1, le=60)

    @field_validator("codex_model", "openai_api_key", "openai_model", mode="before")
    @classmethod
    def blank_optional_strings_are_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def ensure_data_directories(self) -> None:
        for directory in (
            self.browser_profile_dir,
            self.resume_dir,
            self.screenshot_dir,
            self.log_dir,
            self.codex_home_dir,
            self.codex_workspace_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
