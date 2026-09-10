from typing import Literal

from pydantic import BaseModel


class AIConnectionStatus(BaseModel):
    selected_provider: Literal["codex", "openai", "auto", "none"]
    active_provider: Literal["codex", "openai"] | None = None
    ai_configured: bool
    model: str | None = None
    codex_connected: bool = False
    codex_account_type: str | None = None
    codex_email: str | None = None
    codex_plan_type: str | None = None
    codex_runtime_available: bool = True
    codex_error: str | None = None
    openai_api_configured: bool = False
    ai_invocation_count: int
    auto_submit: bool = False
    browser_headless: bool
    data_directory: str


class CodexLoginStart(BaseModel):
    login_id: str
    auth_url: str
    status: Literal["pending"] = "pending"


class CodexLoginStatus(BaseModel):
    login_id: str
    status: Literal["pending", "completed", "failed", "cancelled"]
    error: str | None = None


class CodexLogoutResponse(BaseModel):
    disconnected: bool = True
