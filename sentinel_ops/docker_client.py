"""Thin wrapper over the Docker Engine API.

Every lookup here is scoped to the configured label. Containers without it are
not merely filtered from listings -- they cannot be resolved by name at all, so
no tool can reach them even if the model asks by exact id. This is the
blast-radius boundary for the whole server.

docker-py is synchronous, so every call is pushed to a worker thread. Blocking
the event loop here would stall unrelated tool calls during an incident, which
is exactly when responsiveness matters.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

import docker
from docker.errors import DockerException
from docker.models.containers import Container

from .config import Config


class ServiceNotFoundError(Exception):
    """Raised when a service is absent, or present but outside the managed scope."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f'No managed service named "{name}". It either does not exist or is '
            f"not labelled as managed, which puts it outside this agent's reach."
        )


class RemediationError(Exception):
    """Raised when a state-changing operation cannot be completed safely."""


@dataclass
class ServiceSummary:
    name: str
    id: str
    image: str
    state: str
    status: str
    health: str
    started_at: str | None
    restart_count: int


@dataclass
class ServiceStats:
    name: str
    cpu_percent: float | None
    memory_used_mb: float | None
    memory_limit_mb: float | None
    memory_percent: float | None


def _round(value: float) -> float:
    return round(value, 2)


def _strip_slash(name: str) -> str:
    return name[1:] if name.startswith("/") else name


def _demultiplex(raw: bytes) -> str:
    """Strip Docker's stream framing.

    Each frame is an 8-byte header whose last four bytes are a big-endian
    payload length. docker-py returns already-clean bytes in some code paths, so
    this falls back to a plain decode when the framing does not look well-formed.
    """
    chunks: list[str] = []
    offset = 0
    while offset + 8 <= len(raw):
        if raw[offset] not in (0, 1, 2):
            return raw.decode("utf-8", errors="replace")
        length = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        if offset + 8 + length > len(raw):
            break
        chunks.append(
            raw[offset + 8 : offset + 8 + length].decode("utf-8", errors="replace")
        )
        offset += 8 + length

    if not chunks:
        return raw.decode("utf-8", errors="replace")
    return "".join(chunks)


class DockerClient:
    def __init__(self, config: Config) -> None:
        self._client = docker.from_env()
        self._label = config.label

    async def ping(self) -> None:
        """Verify the daemon is reachable, so startup fails loudly, not per-request."""
        await asyncio.to_thread(self._client.ping)

    def _list_managed(self) -> list[Container]:
        return self._client.containers.list(
            all=True, filters={"label": self._label.as_filter()}
        )

    async def list_services(self) -> list[dict[str, Any]]:
        containers = await asyncio.to_thread(self._list_managed)

        summaries = [
            ServiceSummary(
                name=_strip_slash(container.name),
                id=container.id[:12],
                image=container.attrs["Config"]["Image"],
                state=container.attrs["State"]["Status"],
                status=container.status,
                health=(container.attrs["State"].get("Health") or {}).get(
                    "Status", "none"
                ),
                started_at=container.attrs["State"].get("StartedAt"),
                restart_count=container.attrs.get("RestartCount", 0),
            )
            for container in containers
        ]
        summaries.sort(key=lambda s: s.name)
        return [asdict(s) for s in summaries]

    def _resolve_sync(self, name: str) -> Container:
        """Resolve a service name to a container, refusing anything unlabelled.

        All write paths must go through this rather than addressing Docker
        directly.
        """
        wanted = _strip_slash(name).lower()
        for container in self._list_managed():
            if (
                container.id == name
                or container.id.startswith(wanted)
                or _strip_slash(container.name).lower() == wanted
            ):
                return container
        raise ServiceNotFoundError(name)

    async def resolve(self, name: str) -> Container:
        return await asyncio.to_thread(self._resolve_sync, name)

    async def inspect(self, name: str) -> dict[str, Any]:
        container = await self.resolve(name)
        return container.attrs

    async def logs(self, name: str, tail: int, since_seconds: int | None = None) -> str:
        container = await self.resolve(name)

        kwargs: dict[str, Any] = {
            "stdout": True,
            "stderr": True,
            "tail": tail,
            "timestamps": True,
        }
        if since_seconds is not None:
            kwargs["since"] = int(time.time()) - since_seconds

        raw = await asyncio.to_thread(lambda: container.logs(**kwargs))
        return _demultiplex(raw if isinstance(raw, bytes) else bytes(raw))

    async def stats(self, name: str) -> dict[str, Any]:
        """Single-shot resource sample.

        CPU percentage needs two data points; on a container's first sample
        Docker reports an empty previous reading, so this returns None rather
        than a misleading zero.
        """
        container = await self.resolve(name)
        sample = await asyncio.to_thread(lambda: container.stats(stream=False))

        cpu = sample.get("cpu_stats") or {}
        precpu = sample.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (
            precpu.get("cpu_usage") or {}
        ).get("total_usage", 0)
        system_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
        cores = cpu.get("online_cpus") or 1

        memory = sample.get("memory_stats") or {}
        used = memory.get("usage")
        limit = memory.get("limit")

        return asdict(
            ServiceStats(
                name=_strip_slash(name),
                cpu_percent=(
                    _round((cpu_delta / system_delta) * cores * 100)
                    if system_delta > 0 and cpu_delta > 0
                    else None
                ),
                memory_used_mb=_round(used / 1024 / 1024) if used else None,
                memory_limit_mb=_round(limit / 1024 / 1024) if limit else None,
                memory_percent=_round((used / limit) * 100) if used and limit else None,
            )
        )

    async def restart(self, name: str, timeout: int = 10) -> None:
        """Restart a service in place. Config and image are unchanged."""
        container = await self.resolve(name)
        await asyncio.to_thread(lambda: container.restart(timeout=timeout))

    async def set_memory_limit(self, name: str, megabytes: int) -> float | None:
        """Raise the memory ceiling of a running service.

        Swap is pinned to the same value: left alone, Docker grants swap equal
        to twice the limit, which turns an out-of-memory crash into silent
        thrashing that is harder to diagnose.

        Returns the previous limit in MB, or None if it was unlimited.
        """
        container = await self.resolve(name)
        previous = container.attrs["HostConfig"].get("Memory") or 0
        limit = f"{megabytes}m"

        await asyncio.to_thread(
            lambda: container.update(mem_limit=limit, memswap_limit=limit)
        )
        return _round(previous / 1024 / 1024) if previous else None

    async def recreate_with_image(self, name: str, image: str) -> str:
        """Replace a service with the same configuration on a different image tag.

        Docker cannot swap the image of an existing container, so this destroys
        and rebuilds it. That makes it the one genuinely unrecoverable operation
        here, and it is ordered accordingly: the replacement image is verified
        present before anything is torn down, and a failed rebuild is retried
        once on the original image so a bad tag cannot leave the service simply
        gone.

        Returns the image that was replaced.
        """
        container = await self.resolve(name)
        info = container.attrs
        previous_image = info["Config"]["Image"]

        if previous_image == image:
            raise RemediationError(
                f"{name} already runs {image}; nothing to roll back to."
            )

        # Verified before teardown: pulling here could stall for minutes mid-incident.
        available = await asyncio.to_thread(
            lambda: self._client.images.list(filters={"reference": image})
        )
        if not available:
            raise RemediationError(
                f'Image "{image}" is not present locally. Pull it first; this tool '
                f"will not fetch images while a service is being rebuilt."
            )

        # The label must survive, or the rebuilt service falls outside managed
        # scope and no tool here can reach it again.
        labels = info["Config"].get("Labels") or {}
        if labels.get(self._label.key) != self._label.value:
            raise RemediationError(
                f"Refusing to rebuild {name}: its managed label is missing, so the "
                f"replacement would be unreachable by this agent."
            )

        def rebuild(target_image: str) -> None:
            """Recreate the container on target_image, preserving everything else.

            HostConfig carries ports, mounts, restart policy and resource
            limits, so passing it through wholesale is what keeps the
            replacement faithful to the original.
            """
            created = self._client.api.create_container(
                image=target_image,
                command=info["Config"].get("Cmd"),
                entrypoint=info["Config"].get("Entrypoint"),
                environment=info["Config"].get("Env"),
                labels=labels,
                working_dir=info["Config"].get("WorkingDir") or None,
                user=info["Config"].get("User") or None,
                ports=list(info["Config"].get("ExposedPorts") or {}),
                name=_strip_slash(info["Name"]),
                host_config=info["HostConfig"],
            )
            self._client.api.start(created["Id"])

        await asyncio.to_thread(lambda: container.stop(timeout=10))
        await asyncio.to_thread(lambda: container.remove(force=True))

        try:
            await asyncio.to_thread(lambda: rebuild(image))
        except (DockerException, OSError) as exc:
            try:
                await asyncio.to_thread(lambda: rebuild(previous_image))
                restored = True
            except (DockerException, OSError):
                restored = False

            raise RemediationError(
                f"Rebuilding {name} on {image} failed: {exc}. "
                + (
                    f"The service was restored on {previous_image}."
                    if restored
                    else f"THE SERVICE IS DOWN and could not be restored on "
                    f"{previous_image}. This needs manual recovery now."
                )
            ) from exc

        return previous_image
