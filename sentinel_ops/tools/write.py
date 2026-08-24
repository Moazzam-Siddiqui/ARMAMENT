"""State-changing remediation tools.

Every tool here is annotated ``destructive_hint=True``. TrueForge resolves its
``require_approval_for_tools: ["@destructive"]`` policy against these
annotations, so the annotation is not documentation -- it is the mechanism that
puts a human in front of the action. A tool moved into this file without the
annotation is silently ungated, which is why review treats a missing or
dishonest annotation here as a blocking defect.

Each tool also requires a free-text ``reason``. The approval dialog renders tool
arguments, so this puts the agent's justification in front of the person
deciding, and the same text lands in the audit log.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..audit import record
from ..docker_client import DockerClient

DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)

ServiceName = Annotated[str, Field(description="Name of the managed service", min_length=1)]

Reason = Annotated[
    str,
    Field(
        min_length=10,
        description=(
            "Why this action is needed, based on evidence you gathered. Shown to "
            "the human approving it and written to the audit log."
        ),
    ),
]


def register_write_tools(mcp: MCPServer, docker: DockerClient) -> None:
    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Restart a service in place, keeping its image and configuration. "
            "Use this for a service that has crashed, hung, or exhausted a pool "
            "of connections. In-flight requests will be dropped. Investigate the "
            "cause before restarting: a restart hides the evidence in memory."
        ),
    )
    async def restart_service(service: ServiceName, reason: Reason) -> str:
        try:
            await docker.restart(service)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as text
            detail = str(exc)
            await record("restart_service", service, reason, "failed", error=detail)
            # Raised, not returned: the audit entry is written first so the
            # attempt is recorded even though the call reports as an error.
            raise ToolError(detail) from exc

        await record("restart_service", service, reason, "succeeded")
        return (
            f"Restarted {service}. Confirm recovery with get_service_health "
            f"before reporting the incident resolved."
        )

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Increase the memory ceiling of a running service, applied without a "
            "restart. Use this only when evidence shows the service was killed "
            "for exceeding its limit: get_service_health reports oomKilled, or "
            "get_service_stats shows memory near the limit. Raising the ceiling "
            "relieves the symptom; a genuine leak will reach the new limit too."
        ),
    )
    async def raise_memory_limit(
        service: ServiceName,
        limit_mb: Annotated[
            int,
            Field(description="New memory ceiling in megabytes", ge=16, le=32768),
        ],
        reason: Reason,
    ) -> str:
        try:
            previous_mb = await docker.set_memory_limit(service, limit_mb)
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            await record("raise_memory_limit", service, reason, "failed", error=detail)
            # Raised, not returned: the audit entry is written first so the
            # attempt is recorded even though the call reports as an error.
            raise ToolError(detail) from exc

        await record(
            "raise_memory_limit",
            service,
            reason,
            "succeeded",
            detail={"previousMb": previous_mb, "newMb": limit_mb},
        )
        was = "unlimited" if previous_mb is None else f"{previous_mb} MB"
        return (
            f"Memory limit for {service} set to {limit_mb} MB (was {was}). Watch "
            f"get_service_stats: if usage climbs back to the new ceiling, this is "
            f"a leak and not an undersized limit."
        )

    @mcp.tool(
        annotations=DESTRUCTIVE,
        description=(
            "Rebuild a service on a different image tag, keeping its ports, "
            "mounts, environment and restart policy. Use this when an incident "
            "began right after a deploy and the logs point at new code. This is "
            "the most disruptive action available: the container is destroyed "
            "and rebuilt, so anything written inside it that is not on a mounted "
            "volume is lost. The target image must already be present locally."
        ),
    )
    async def rollback_deploy(
        service: ServiceName,
        image: Annotated[
            str,
            Field(
                min_length=1,
                description="Image tag to roll back to, for example checkout-api:1.4.2",
            ),
        ],
        reason: Reason,
    ) -> str:
        try:
            previous_image = await docker.recreate_with_image(service, image)
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            await record("rollback_deploy", service, reason, "failed", error=detail)
            # Raised, not returned: the audit entry is written first so the
            # attempt is recorded even though the call reports as an error.
            raise ToolError(detail) from exc

        await record(
            "rollback_deploy",
            service,
            reason,
            "succeeded",
            detail={"previousImage": previous_image, "newImage": image},
        )
        return (
            f"Rebuilt {service} on {image} (was {previous_image}). Confirm "
            f"recovery with get_service_health, and check search_logs for "
            f"startup errors."
        )
