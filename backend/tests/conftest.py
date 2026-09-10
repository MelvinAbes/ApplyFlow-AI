from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite://",
        browser_headless=True,
        browser_profile_dir=tmp_path / "browser-profile",
        resume_dir=tmp_path / "resumes",
        screenshot_dir=tmp_path / "screenshots",
        codex_home_dir=tmp_path / "codex",
        codex_workspace_dir=tmp_path / "codex-workspace",
        ai_provider="none",
        openai_api_key=None,
        openai_model=None,
    )
