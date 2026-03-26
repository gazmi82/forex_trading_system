from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_APP_LOG_HANDLER = "forex_app_file"
_ERROR_LOG_HANDLER = "forex_error_file"
_CONSOLE_HANDLER = "forex_console"


def configure_app_logging(log_dir: Path | None = None, *, level: int = logging.INFO) -> None:
    """
    Configure durable application logging once for both CLI and API runtimes.

    We keep stdout logs for operator visibility, then add rotating file logs so
    background failures remain available after the terminal session is gone.
    """
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    if not _has_handler(root, _CONSOLE_HANDLER):
        console = logging.StreamHandler(sys.stdout)
        console.set_name(_CONSOLE_HANDLER)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    if not _has_handler(root, _APP_LOG_HANDLER):
        app_file = RotatingFileHandler(
            output_dir / "app.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        app_file.set_name(_APP_LOG_HANDLER)
        app_file.setLevel(level)
        app_file.setFormatter(formatter)
        root.addHandler(app_file)

    if not _has_handler(root, _ERROR_LOG_HANDLER):
        error_file = RotatingFileHandler(
            output_dir / "errors.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        error_file.set_name(_ERROR_LOG_HANDLER)
        error_file.setLevel(logging.WARNING)
        error_file.setFormatter(formatter)
        root.addHandler(error_file)


def record_runtime_event(
    *,
    component: str,
    action: str,
    message: str,
    level: str = "ERROR",
    context: dict[str, Any] | None = None,
    exc: Exception | None = None,
    log_dir: Path | None = None,
) -> Path:
    """
    Append one structured runtime event to `runtime_events.jsonl`.

    This complements normal logger output with a compact machine-readable record
    that is easier to search after crashes, background failures, or API issues.
    """
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "runtime_events.jsonl"

    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": str(level).upper(),
        "component": component,
        "action": action,
        "message": message,
    }
    if context:
        payload["context"] = _make_json_safe(context)
    if exc is not None:
        payload["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).strip(),
        }

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    try:
        from app.core.runtime_alerts import process_runtime_event_for_alerts

        process_runtime_event_for_alerts(payload, log_dir=output_dir)
    except Exception:
        # Alert generation must never block or break the original runtime-event
        # write path. The event log remains the source of truth.
        pass

    return path


def _has_handler(logger: logging.Logger, handler_name: str) -> bool:
    return any(handler.get_name() == handler_name for handler in logger.handlers)


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)
