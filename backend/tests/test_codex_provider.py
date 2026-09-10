from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated.v2_all import ChatgptAccount

from app.ai.codex_runtime import CodexRuntime
from app.ai.providers import CodexProvider, OpenAIProvider, resolve_ai_provider
from app.schemas.application import AIAnswer


class FakeThread:
    def __init__(self) -> None:
        self.run_kwargs = None

    async def run(self, prompt, **kwargs):
        self.prompt = prompt
        self.run_kwargs = kwargs
        return SimpleNamespace(
            final_response=(
                '{"status":"ANSWERED","answer":"Python experience.",'
                '"grounded_facts":["Python experience"],"confidence":0.9}'
            )
        )


class FakeCodex:
    def __init__(self, config) -> None:
        self.config = config
        self.initialized = False
        self.closed = False
        self.thread = FakeThread()
        self.thread_kwargs = None
        self.account_value = ChatgptAccount(
            email="candidate@example.com",
            planType="plus",
            type="chatgpt",
        )

    async def _ensure_initialized(self) -> None:
        self.initialized = True

    async def account(self, *, refresh_token=False):
        return SimpleNamespace(account=SimpleNamespace(root=self.account_value))

    async def thread_start(self, **kwargs):
        self.thread_kwargs = kwargs
        return self.thread

    async def models(self):
        return SimpleNamespace(data=[SimpleNamespace(model="account-default", is_default=True)])

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_codex_runtime_uses_managed_account_and_locked_down_structured_turn(
    test_settings, monkeypatch
) -> None:
    created = []

    def factory(config):
        client = FakeCodex(config)
        created.append(client)
        return client

    monkeypatch.setattr("app.ai.codex_runtime.AsyncCodex", factory)
    settings = test_settings.model_copy(
        update={"ai_provider": "codex", "codex_model": "test-codex"}
    )
    runtime = CodexRuntime(settings)

    account = await runtime.account_status()
    value, output_characters, model = await runtime.structured_output(
        instructions="Use supplied facts only.",
        prompt="Python experience",
        output_model=AIAnswer,
    )

    client = created[0]
    assert account.connected is True
    assert account.account_type == "chatgpt"
    assert account.plan_type == "plus"
    assert client.config.env["CODEX_HOME"] == str(settings.codex_home_dir)
    assert client.thread_kwargs["ephemeral"] is True
    assert "Do not call tools" in client.thread_kwargs["base_instructions"]
    assert client.thread_kwargs["approval_mode"] == ApprovalMode.deny_all
    assert client.thread_kwargs["sandbox"] == Sandbox.read_only
    assert client.thread.run_kwargs["approval_mode"] == ApprovalMode.deny_all
    assert client.thread.run_kwargs["sandbox"] == Sandbox.read_only
    output_schema = client.thread.run_kwargs["output_schema"]
    assert output_schema["type"] == "object"
    assert output_schema["additionalProperties"] is False
    assert set(output_schema["required"]) == set(output_schema["properties"])
    assert "default" not in output_schema["properties"]["answer"]
    assert value.answer == "Python experience."
    assert output_characters > 0
    assert model == "test-codex"
    await runtime.close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_blank_codex_model_uses_account_default(test_settings, monkeypatch) -> None:
    created = []

    def factory(config):
        client = FakeCodex(config)
        created.append(client)
        return client

    monkeypatch.setattr("app.ai.codex_runtime.AsyncCodex", factory)
    settings = test_settings.model_copy(update={"ai_provider": "codex", "codex_model": ""})
    runtime = CodexRuntime(settings)

    _, _, model = await runtime.structured_output(
        instructions="Use supplied facts only.",
        prompt="Python experience",
        output_model=AIAnswer,
    )

    assert model == "account-default"
    assert created[0].thread_kwargs["model"] == "account-default"


def test_blank_optional_ai_settings_are_normalized(test_settings) -> None:
    settings = type(test_settings)(
        codex_model=" ",
        openai_api_key="",
        openai_model="  ",
    )
    assert settings.codex_model is None
    assert settings.openai_api_key is None
    assert settings.openai_model is None


class FakeRuntime:
    def __init__(self, connected: bool):
        self.connected = connected

    async def account_status(self):
        return SimpleNamespace(connected=self.connected)


@pytest.mark.asyncio
async def test_provider_selection_prefers_codex_and_can_fall_back_to_api(test_settings) -> None:
    codex_settings = test_settings.model_copy(update={"ai_provider": "codex"})
    selected = await resolve_ai_provider(codex_settings, FakeRuntime(connected=True))
    assert isinstance(selected, CodexProvider)

    auto_settings = test_settings.model_copy(
        update={
            "ai_provider": "auto",
            "openai_api_key": "test-key",
            "openai_model": "test-model",
        }
    )
    fallback = await resolve_ai_provider(auto_settings, FakeRuntime(connected=False))
    assert isinstance(fallback, OpenAIProvider)

    unavailable = await resolve_ai_provider(codex_settings, FakeRuntime(connected=False))
    assert unavailable is None
