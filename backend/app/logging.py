import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("job_assistant")


def configure_operational_log(directory: Path, level: int) -> None:
    """Persist sanitized operational events without profiles, answers, or browser data."""
    directory.mkdir(parents=True, exist_ok=True)
    logger.setLevel(level)
    if any(getattr(handler, "_job_assistant_file", False) for handler in logger.handlers):
        return
    handler = RotatingFileHandler(
        directory / "operations.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler._job_assistant_file = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def log_event(event: str, *, entity_id: str | None = None, **safe_details: Any) -> None:
    """Log operational metadata only; callers must not pass PII or answer values."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "entity_id": entity_id,
        **safe_details,
    }
    logger.info(json.dumps(payload, default=str, separators=(",", ":")))


def sanitized_error(error: Exception) -> str:
    # Exception messages from websites/SDKs can contain inputs. Persist the type only.
    return f"{type(error).__name__}: operation failed; inspect server logs and screenshot metadata"
