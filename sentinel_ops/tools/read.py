"""Read-only investigation tools.

Every tool here is annotated ``read_only_hint=True``. The harness derives its
approval policy from these annotations, so the labels must stay honest: a tool
that mutates anything does not belong in this file.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..docker_client import DockerClient

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

ServiceName = Annotated[str, Field(description="Name of the managed service", min_length=1)]


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _fail(error: Exception) -> ToolError:
    """Surface an error the model can act on, rather than a stack trace.

    Raised rather than returned so the result carries the protocol's isError
    flag: a failure that reads as ordinary output invites the model to treat it
    as a finding.
    """
    return ToolError(str(error))


def register_read_tools(mcp: MCPServer, docker: DockerClient) -> None:
    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "List every managed service with its current state, health, uptime "
            "and restart count. Start here when investigating an incident: it "
            "shows which services exist and which are unhealthy."
        ),
    )
    async def list_services() -> str:
        try:
            services = await docker.list_services()
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as text
            raise _fail(exc) from exc

        if not services:
            return (
                "No managed services found. Services must carry the manager's "
                "label to be visible to this agent."
            )
        return _json(services)

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Detailed health for one service: state, exit code, health-check "
            "history, restart count, and whether it was killed for running out "
            "of memory. Use this after list_services to understand why a "
            "specific service is failing."
        ),
    )
    async def get_service_health(service: ServiceName) -> str:
        try:
            info = await docker.inspect(service)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

        state = info["State"]
        health = state.get("Health")

        return _json(
            {
                "name": info["Name"].lstrip("/"),
                "image": info["Config"]["Image"],
                "state": state["Status"],
                "running": state["Running"],
                "exitCode": state["ExitCode"],
                "oomKilled": state["OOMKilled"],
                "restartCount": info.get("RestartCount", 0),
                "startedAt": state.get("StartedAt"),
                "finishedAt": state.get("FinishedAt"),
                "error": state.get("Error") or None,
                "health": (
                    {
                        "status": health.get("Status"),
                        "failingStreak": health.get("FailingStreak"),
                        # Only the last few probes matter; the full log is long
                        # and repetitive.
                        "recentChecks": [
                            {
                                "start": entry.get("Start"),
                                "exitCode": entry.get("ExitCode"),
                                "output": (entry.get("Output") or "")[:500],
                            }
                            for entry in (health.get("Log") or [])[-5:]
                        ],
                    }
                    if health
                    else None
                ),
            }
        )

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Fetch recent log lines for a service, optionally filtered by a "
            "case-insensitive substring and a time window. Use this to find the "
            "error behind a failing health check."
        ),
    )
    async def search_logs(
        service: ServiceName,
        contains: Annotated[
            str | None,
            Field(description="Only return lines containing this text (case-insensitive)"),
        ] = None,
        tail: Annotated[
            int, Field(description="How many recent lines to scan", ge=1, le=1000)
        ] = 200,
        since_seconds: Annotated[
            int | None, Field(description="Only scan lines from the last N seconds", ge=1)
        ] = None,
    ) -> str:
        try:
            raw = await docker.logs(service, tail, since_seconds)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

        lines = [line for line in raw.split("\n") if line.strip()]
        matched = (
            [line for line in lines if contains.lower() in line.lower()]
            if contains
            else lines
        )

        if not matched:
            if contains:
                return (
                    f'No lines containing "{contains}" in the last {tail} lines '
                    f"of {service}."
                )
            return f"No log output for {service}."
        return "\n".join(matched)

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Current CPU and memory usage for a service. Use this to confirm or "
            "rule out resource exhaustion as the cause of an incident."
        ),
    )
    async def get_service_stats(service: ServiceName) -> str:
        try:
            return _json(await docker.stats(service))
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc
