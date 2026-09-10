from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.ai.client import get_openai_client
from app.ai.codex_runtime import CodexRuntime, get_codex_runtime
from app.config import Settings


@dataclass(slots=True)
class StructuredAIResult[OutputModel: BaseModel]:
    value: OutputModel
    provider: str
    model: str
    output_characters: int


class StructuredAIProvider(Protocol):
    name: str
    model: str

    @property
    def cache_identity(self) -> str: ...

    async def generate[OutputModel: BaseModel](
        self,
        *,
        instructions: str,
        prompt: str,
        output_model: type[OutputModel],
    ) -> StructuredAIResult[OutputModel]: ...


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.openai_model or "unconfigured"

    @property
    def cache_identity(self) -> str:
        return f"openai:{self.model}"

    async def generate[OutputModel: BaseModel](
        self,
        *,
        instructions: str,
        prompt: str,
        output_model: type[OutputModel],
    ) -> StructuredAIResult[OutputModel]:
        client = get_openai_client(self.settings)
        response = await client.responses.parse(
            model=self.model,
            store=False,
            input=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            text_format=output_model,
        )
        if response.output_parsed is None:
            raise ValueError("Model returned no validated structured output")
        return StructuredAIResult(
            value=response.output_parsed,
            provider=self.name,
            model=self.model,
            output_characters=len(response.output_text or ""),
        )


class CodexProvider:
    name = "codex"

    def __init__(self, settings: Settings, runtime: CodexRuntime):
        self.settings = settings
        self.runtime = runtime
        self.model = settings.codex_model or "codex-default"

    @property
    def cache_identity(self) -> str:
        return f"codex:{self.model}"

    async def generate[OutputModel: BaseModel](
        self,
        *,
        instructions: str,
        prompt: str,
        output_model: type[OutputModel],
    ) -> StructuredAIResult[OutputModel]:
        value, output_characters, model = await self.runtime.structured_output(
            instructions=instructions,
            prompt=prompt,
            output_model=output_model,
        )
        return StructuredAIResult(
            value=value,
            provider=self.name,
            model=model,
            output_characters=output_characters,
        )


async def resolve_ai_provider(
    settings: Settings, runtime: CodexRuntime | None = None
) -> StructuredAIProvider | None:
    if settings.ai_provider == "none":
        return None
    if settings.ai_provider in {"codex", "auto"}:
        codex_runtime = runtime or get_codex_runtime()
        if (await codex_runtime.account_status()).connected:
            return CodexProvider(settings, codex_runtime)
        if settings.ai_provider == "codex":
            return None
    if settings.ai_provider in {"openai", "auto"}:
        if settings.openai_api_key and settings.openai_model:
            return OpenAIProvider(settings)
    return None
