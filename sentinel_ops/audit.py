"""Append-only, size-bounded record of every state-changing action."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("sentinel-ops")
_audit_lock = threading.RLock()
MAX_BYTES = 10 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024
BACKUP_COUNT = 5


def _audit_path() -> Path:
    return Path(os.environ.get("ARMAMENT_AUDIT_LOG", "sentinel-audit.log"))


def _secure_file(path: Path) -> None:
    """Restrict existing and newly created audit files to the owner."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        log.warning("could not restrict audit file permissions: %s", path)


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    oldest = path.with_name(path.name + f".{BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(path.name + f".{index}")
        if source.exists():
            source.replace(path.with_name(path.name + f".{index + 1}"))
    path.replace(path.with_name(path.name + ".1"))
    for index in range(1, BACKUP_COUNT + 1):
        backup = path.with_name(path.name + f".{index}")
        if backup.exists():
            _secure_file(backup)


def _append(line: str) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (line + "\n").encode("utf-8")
    if len(encoded) > MAX_ENTRY_BYTES:
        event = json.loads(line)
        event["detail"] = {"truncated": True}
        event["reason"] = str(event.get("reason", ""))[:2048]
        if "error" in event:
            event["error"] = str(event["error"])[:2048]
        line = json.dumps(event, ensure_ascii=True)
        encoded = (line + "\n").encode("utf-8")
    if len(encoded) > MAX_ENTRY_BYTES:
        raise ValueError("audit entry exceeds the maximum permitted size")

    with _audit_lock:
        if path.exists() and path.stat().st_size + len(encoded) > MAX_BYTES:
            _rotate(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        _secure_file(path)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(encoded)


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
        "at": datetime.now(UTC).isoformat(),
        "action": action,
        "service": service,
        "reason": reason,
        "outcome": outcome,
    }
    if detail is not None:
        entry["detail"] = detail
    if error is not None:
        entry["error"] = error

    line = json.dumps(entry, ensure_ascii=True)
    try:
        await asyncio.to_thread(_append, line)
    except (OSError, ValueError, TypeError) as exc:
        log.error("audit write failed: %s", exc)
    log.info("%s", line)


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    """Return the newest retained audit entries across active and rotated logs."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    path = _audit_path()
    events: list[dict[str, Any]] = []
    with _audit_lock:
        paths = [
            path.with_name(path.name + f".{index}")
            for index in range(BACKUP_COUNT, 0, -1)
        ] + [path]
        for source in paths:
            if not source.is_file():
                continue
            _secure_file(source)
            try:
                lines = source.read_bytes().decode("utf-8", errors="replace").splitlines()
            except OSError:
                log.exception("could not read audit file %s", source)
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict):
                    events.append(event)
    return list(reversed(events[-limit:]))
