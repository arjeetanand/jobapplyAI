from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def trace_step(name: str, status: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "data": data or {},
        "at": datetime.now(UTC).isoformat(),
    }


def normalize_trace(raw_steps: list[Any] | None) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for index, item in enumerate(raw_steps or []):
        if isinstance(item, dict):
            trace.append(
                {
                    "name": str(item.get("name") or f"step_{index + 1}"),
                    "status": str(item.get("status") or "completed"),
                    "message": str(item.get("message") or ""),
                    "data": item.get("data") if isinstance(item.get("data"), dict) else {},
                    "at": item.get("at"),
                }
            )
        else:
            trace.append(
                {
                    "name": f"browser_step_{index + 1}",
                    "status": "completed",
                    "message": str(item),
                    "data": {},
                    "at": None,
                }
            )
    return trace


def append_trace(raw_steps: list[Any] | None, name: str, status: str, message: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [*normalize_trace(raw_steps), trace_step(name, status, message, data)]


def trace_messages(raw_steps: list[Any] | None) -> list[str]:
    return [step["message"] or step["name"] for step in normalize_trace(raw_steps)]
