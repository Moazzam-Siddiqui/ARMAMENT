"""Append-only record of every state-changing action.

The harness gates these actions on human approval, but the gate lives outside
this process and leaves no trace here. This log is the server's own account of
what it was asked to do and what happened, written from inside the code path
that performs the work so nothing can act without being recorded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("sentinel-ops")

LOG_PATH = Path.cwd() / "sentinel-audit.log"


def _append(line: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def record(
    action: str,
    service: str,
    reason: str,
    outcome: Literal["succeeded", "failed"],
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write one JSON line describing an attempted action.

    Logging failures are reported but never raised: losing an audit line must
    not turn a successful remediation into a reported failure, which would push
    the agent into retrying an action that already happened.
    """
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
