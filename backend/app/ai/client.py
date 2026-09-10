from openai import AsyncOpenAI

from app.config import Settings


class AIUnavailableError(RuntimeError):
    pass


def get_openai_client(settings: Settings) -> AsyncOpenAI:
    if not settings.openai_api_key or not settings.openai_model:
        raise AIUnavailableError("OPENAI_API_KEY and OPENAI_MODEL are required for AI features")
    return AsyncOpenAI(api_key=settings.openai_api_key)
