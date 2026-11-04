"""Append-only, size-bounded record of every state-changing action."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("sentinel-ops")
_write_lock = threading.Lock()
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def _audit_path() -> Path:
    return Path(os.environ.get("ARMAMENT_AUDIT_LOG", "sentinel-audit.log"))


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_BYTES:
        return
    oldest = path.with_name(path.name + f".{BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(path.name + f".{index}")
        if source.exists():
            source.replace(path.with_name(path.name + f".{index + 1}"))
    path.replace(path.with_name(path.name + ".1"))


def _append(line: str) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        _rotate(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


async def record(
    action: str,
    service: str,
    reason: str,
    outcome: Literal["succeeded", "failed"],
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write one JSON line. Logging failure never triggers an unsafe retry."""
    entry: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "service": service,
        "reason": reason,
        "outcome": outcome,
    }
    if detail is not None:
        entry["detail"] = detail
    if error is not None:
        entry["error"] = error

    line = json.dumps(entry)
    try:
        await asyncio.to_thread(_append, line)
    except OSError as exc:
        log.error("audit write failed: %s", exc)
    log.info("%s", line)


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    """Return the newest audit entries from the active log, newest first."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    path = _audit_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
