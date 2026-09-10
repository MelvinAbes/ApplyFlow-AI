import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ChatgptAccount, ReasoningEffort
from pydantic import BaseModel

from app.ai.client import AIUnavailableError
from app.config import Settings, get_settings

TEXT_ONLY_INSTRUCTIONS = (
    "You are a text-only structured-output component inside a job application assistant. "
    "Use only the text supplied in the current request. Do not call tools, run commands, "
    "read files, inspect the environment, browse, or make network requests. Never infer or "
    "invent candidate facts. Return only the JSON value required by the output schema."
)


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic schema compatible with OpenAI Structured Outputs."""

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return

        properties = node.get("properties")
        if isinstance(properties, dict):
            node["additionalProperties"] = False
            node["required"] = list(properties)

        if node.get("default") is None:
            node.pop("default", None)

        for value in node.values():
            visit(value)

    visit(schema)
    return schema


@dataclass(slots=True)
class CodexAccountStatus:
    connected: bool
    account_type: str | None = None
    email: str | None = None
    plan_type: str | None = None
    runtime_available: bool = True
    error: str | None = None


@dataclass(slots=True)
class LoginAttempt:
    login_id: str
    status: str = "pending"
    error: str | None = None


class CodexRuntime:
    """Owns one local Codex App Server process and its managed ChatGPT session."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: AsyncCodex | None = None
        self._client_lock = asyncio.Lock()
        self._generation_lock = asyncio.Lock()
        self._default_model: str | None = None
        self._login_attempts: dict[str, LoginAttempt] = {}
        self._login_tasks: dict[str, asyncio.Task[None]] = {}

    async def _get_client(self) -> AsyncCodex:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            self.settings.ensure_data_directories()
            config = CodexConfig(
                codex_bin=str(self.settings.codex_bin) if self.settings.codex_bin else None,
                cwd=str(self.settings.codex_workspace_dir),
                env={"CODEX_HOME": str(self.settings.codex_home_dir), **self._safe_locale_env()},
                client_name="job_application_assistant",
                client_title="Job Application Assistant",
                client_version="0.1.0",
                experimental_api=False,
            )
            client = AsyncCodex(config)
            self._client = client
            return client

    @staticmethod
    def _safe_locale_env() -> dict[str, str]:
        return {
            key: value for key in ("LANG", "LC_ALL", "LC_CTYPE") if (value := os.environ.get(key))
        }

    async def account_status(self, *, refresh: bool = False) -> CodexAccountStatus:
        try:
            client = await self._get_client()
            response = await client.account(refresh_token=refresh)
        except Exception as error:
            return CodexAccountStatus(
                connected=False,
                runtime_available=False,
                error=f"Codex runtime unavailable ({type(error).__name__})",
            )
        if response.account is None:
            return CodexAccountStatus(connected=False)
        account = response.account.root
        if isinstance(account, ChatgptAccount):
            return CodexAccountStatus(
                connected=True,
                account_type="chatgpt",
                email=account.email,
                plan_type=account.plan_type.value,
            )
        account_type = getattr(account, "type", type(account).__name__)
        return CodexAccountStatus(connected=True, account_type=str(account_type))

    async def start_chatgpt_login(self) -> tuple[str, str]:
        client = await self._get_client()
        handle = await client.login_chatgpt()
        attempt = LoginAttempt(login_id=handle.login_id)
        self._login_attempts[handle.login_id] = attempt
        self._login_tasks[handle.login_id] = asyncio.create_task(
            self._watch_login(handle.login_id, handle),
            name=f"codex-login-{handle.login_id}",
        )
        return handle.login_id, handle.auth_url

    async def _watch_login(self, login_id: str, handle: Any) -> None:
        attempt = self._login_attempts[login_id]
        try:
            result = await handle.wait()
            if result.success:
                attempt.status = "completed"
            else:
                attempt.status = "failed"
                attempt.error = result.error or "ChatGPT sign-in did not complete"
        except asyncio.CancelledError:
            attempt.status = "cancelled"
            raise
        except Exception as error:
            attempt.status = "failed"
            attempt.error = f"ChatGPT sign-in failed ({type(error).__name__})"
        finally:
            self._login_tasks.pop(login_id, None)

    def login_status(self, login_id: str) -> LoginAttempt | None:
        return self._login_attempts.get(login_id)

    async def logout(self) -> None:
        client = await self._get_client()
        await client.logout()

    async def structured_output[OutputModel: BaseModel](
        self,
        *,
        instructions: str,
        prompt: str,
        output_model: type[OutputModel],
    ) -> tuple[OutputModel, int, str]:
        status = await self.account_status()
        if not status.connected:
            raise AIUnavailableError("Connect a ChatGPT account before using Codex AI features")

        client = await self._get_client()
        async with self._generation_lock:
            model = await self._select_model(client)
            thread = await client.thread_start(
                approval_mode=ApprovalMode.deny_all,
                base_instructions=TEXT_ONLY_INSTRUCTIONS,
                developer_instructions=instructions,
                cwd=str(self.settings.codex_workspace_dir),
                ephemeral=True,
                model=model,
                sandbox=Sandbox.read_only,
                service_name="job_application_assistant",
            )
            result = await thread.run(
                prompt,
                approval_mode=ApprovalMode.deny_all,
                effort=ReasoningEffort(self.settings.codex_effort),
                output_schema=_strict_json_schema(output_model.model_json_schema()),
                sandbox=Sandbox.read_only,
            )
        if not result.final_response:
            raise ValueError("Codex returned no structured output")
        try:
            value = output_model.model_validate_json(result.final_response)
        except Exception:
            value = output_model.model_validate(json.loads(result.final_response))
        return value, len(result.final_response), model

    async def _select_model(self, client: AsyncCodex) -> str:
        configured = (self.settings.codex_model or "").strip()
        if configured:
            return configured
        if self._default_model:
            return self._default_model
        response = await client.models()
        selected = next((item.model for item in response.data if item.is_default), None)
        if selected is None:
            raise AIUnavailableError("The connected ChatGPT account exposes no default Codex model")
        self._default_model = selected
        return selected

    async def close(self) -> None:
        for task in list(self._login_tasks.values()):
            task.cancel()
        if self._login_tasks:
            await asyncio.gather(*self._login_tasks.values(), return_exceptions=True)
        self._login_tasks.clear()
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._default_model = None


_runtime: CodexRuntime | None = None


def get_codex_runtime() -> CodexRuntime:
    global _runtime
    if _runtime is None:
        _runtime = CodexRuntime(get_settings())
    return _runtime
