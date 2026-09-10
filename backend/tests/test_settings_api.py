from types import SimpleNamespace

import pytest

from app.api.settings import settings_status, start_codex_login
from app.models import AIInvocation


class FakeRuntime:
    async def account_status(self):
        return SimpleNamespace(
            connected=True,
            account_type="chatgpt",
            email="candidate@example.com",
            plan_type="plus",
            runtime_available=True,
            error=None,
        )

    async def start_chatgpt_login(self):
        return "login-1", "https://chatgpt.com/auth/codex"


@pytest.mark.asyncio
async def test_settings_status_reports_codex_as_active_provider(session, test_settings) -> None:
    session.add(
        AIInvocation(
            purpose="test",
            model="codex:test",
            input_characters=10,
            output_characters=5,
        )
    )
    session.commit()
    settings = test_settings.model_copy(update={"ai_provider": "codex"})

    result = await settings_status(session, settings, FakeRuntime())

    assert result.ai_configured is True
    assert result.active_provider == "codex"
    assert result.codex_connected is True
    assert result.codex_plan_type == "plus"
    assert result.ai_invocation_count == 1


@pytest.mark.asyncio
async def test_chatgpt_login_returns_only_trusted_openai_url() -> None:
    result = await start_codex_login(FakeRuntime())
    assert result.login_id == "login-1"
    assert result.auth_url.startswith("https://chatgpt.com/")
