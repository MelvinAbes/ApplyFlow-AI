import ipaddress
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.codex_runtime import get_codex_runtime
from app.api import analytics, applications, demo, jobs, profile, resumes
from app.api import settings as settings_api
from app.api.dependencies import browser_manager
from app.config import get_settings
from app.database import create_db_and_tables
from app.logging import configure_operational_log

settings = get_settings()
log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
logging.basicConfig(level=log_level)
configure_operational_log(settings.log_dir, log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_data_directories()
    create_db_and_tables()
    yield
    await browser_manager.close()
    await get_codex_runtime().close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Local-first, human-approved job application automation",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        {
            settings.frontend_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_local_client(request: Request, call_next):
    client_host = request.client.host if request.client else None
    if client_host and client_host != "testclient":
        try:
            is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = client_host == "localhost"
        if not is_loopback:
            return JSONResponse(status_code=403, content={"detail": "Local access only"})

    origin = request.headers.get("origin")
    allowed_origins = {
        settings.frontend_origin.rstrip("/"),
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        settings.app_base_url.rstrip("/"),
    }
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        if origin.rstrip("/") not in allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
    return await call_next(request)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "auto_submit": False}


for api_router in (
    profile.router,
    resumes.router,
    jobs.router,
    applications.router,
    analytics.router,
    settings_api.router,
):
    app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(demo.router)
