from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, func, select

from app.ai.codex_runtime import CodexRuntime, get_codex_runtime
from app.config import Settings, get_settings
from app.database import get_session
from app.models import AIInvocation
from app.schemas.settings import (
    AIConnectionStatus,
    CodexLoginStart,
    CodexLoginStatus,
    CodexLogoutResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/status", response_model=AIConnectionStatus)
async def settings_status(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    runtime: CodexRuntime = Depends(get_codex_runtime),
) -> AIConnectionStatus:
    calls = session.exec(select(func.count()).select_from(AIInvocation)).one()
    codex = await runtime.account_status()
    api_configured = bool(settings.openai_api_key and settings.openai_model)
    active_provider: str | None = None
    model: str | None = None
    if settings.ai_provider in {"codex", "auto"} and codex.connected:
        active_provider = "codex"
        model = settings.codex_model or "Codex plan default"
    elif settings.ai_provider in {"openai", "auto"} and api_configured:
        active_provider = "openai"
        model = settings.openai_model
    return AIConnectionStatus(
        selected_provider=settings.ai_provider,
        active_provider=active_provider,
        ai_configured=active_provider is not None,
        model=model,
        codex_connected=codex.connected,
        codex_account_type=codex.account_type,
        codex_email=codex.email,
        codex_plan_type=codex.plan_type,
        codex_runtime_available=codex.runtime_available,
        codex_error=codex.error,
        openai_api_configured=api_configured,
        ai_invocation_count=calls,
        auto_submit=False,
        browser_headless=settings.browser_headless,
        data_directory=str(settings.resume_dir.parent),
    )


@router.post("/codex/login", response_model=CodexLoginStart, status_code=status.HTTP_202_ACCEPTED)
async def start_codex_login(
    runtime: CodexRuntime = Depends(get_codex_runtime),
) -> CodexLoginStart:
    try:
        login_id, auth_url = await runtime.start_chatgpt_login()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not start ChatGPT sign-in ({type(error).__name__})",
        ) from error
    parsed = urlparse(auth_url)
    hostname = (parsed.hostname or "").casefold()
    trusted = parsed.scheme == "https" and (
        hostname == "chatgpt.com"
        or hostname.endswith(".chatgpt.com")
        or hostname == "openai.com"
        or hostname.endswith(".openai.com")
    )
    if not trusted:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Codex returned an untrusted sign-in URL",
        )
    return CodexLoginStart(login_id=login_id, auth_url=auth_url)


@router.get("/codex/login/{login_id}", response_model=CodexLoginStatus)
def codex_login_status(
    login_id: str,
    runtime: CodexRuntime = Depends(get_codex_runtime),
) -> CodexLoginStatus:
    attempt = runtime.login_status(login_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login attempt not found")
    return CodexLoginStatus(
        login_id=attempt.login_id,
        status=attempt.status,
        error=attempt.error,
    )


@router.delete("/codex/session", response_model=CodexLogoutResponse)
async def disconnect_codex(
    runtime: CodexRuntime = Depends(get_codex_runtime),
) -> CodexLogoutResponse:
    try:
        await runtime.logout()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not disconnect Codex ({type(error).__name__})",
        ) from error
    return CodexLogoutResponse()
