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
from docker.errors import APIError, DockerException, NotFound
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
        # One lock per service. Two destructive calls on the same container can
        # otherwise interleave -- a rollback removing it while a restart is
        # mid-flight -- and leave the service in a state neither call intended.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, name: str) -> asyncio.Lock:
        """The lock guarding state changes to one service.

        Keyed on the normalised name so "checkout-api" and "/checkout-api"
        cannot each take a different lock for the same container.
        """
        key = _strip_slash(name).lower()
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _force_remove_by_name(self, name: str) -> None:
        """Remove a container by name, tolerating its absence.

        Used to clear a half-built replacement before a fallback rebuild, since
        a container that was created but failed to start still holds the name.
        """
        try:
            self._client.api.remove_container(name, force=True)
        except (NotFound, APIError):
            pass

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
        """Resolve an exact service name or an unambiguous, 12+ char ID prefix.

        Short ID prefixes are too easy to guess accidentally and can select an
        unrelated managed container before the caller notices. All write paths
        go through this method rather than addressing Docker directly.
        """
        wanted = _strip_slash(name).lower()
        containers = self._list_managed()

        # Exact names are unambiguous and remain convenient for operators.
        for container in containers:
            if _strip_slash(container.name).lower() == wanted:
                return container

        # Docker displays 12-character short IDs. Require at least that much
        # entropy and reject collisions rather than silently selecting the
        # first match.
        if len(wanted) >= 12:
            matches = [
                container
                for container in containers
                if container.id.lower().startswith(wanted)
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ServiceNotFoundError(
                    f"{name} is an ambiguous container ID prefix; use a longer ID."
                )

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

    async def restart(self, name: str, timeout_seconds: int = 10) -> None:
        """Restart a service in place. Config and image are unchanged."""
        async with self._lock_for(name):
            # Resolved inside the lock: a concurrent rollback may have rebuilt
            # this service since the call was issued.
            container = await self.resolve(name)
            await asyncio.to_thread(lambda: container.restart(timeout=timeout_seconds))

    async def set_memory_limit(self, name: str, megabytes: int) -> float | None:
        """Raise the memory ceiling of a running service.

        Swap is pinned to the same value: left alone, Docker grants swap equal
        to twice the limit, which turns an out-of-memory crash into silent
        thrashing that is harder to diagnose.

        Returns the previous limit in MB, or None if it was unlimited.
        """
        async with self._lock_for(name):
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
        gone. A rebuild that got as far as creating a container is cleared
        first, because that container still holds the service name and would
        make the fallback fail on a name conflict.

        Returns the image that was replaced.
        """
        async with self._lock_for(name):
            return await self._recreate_locked(name, image)

    def _build_networking_config(
        self, info: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[tuple[str, dict[str, Any]]]]:
        """Build one create-time network and a list of later attachments.

        Docker Engine accepts only one network in NetworkingConfig during
        container creation. Additional networks must be attached afterwards.
        Resolve every network before teardown so missing networks cannot turn
        a rollback into an avoidable outage.
        """
        attached = info.get("NetworkSettings", {}).get("Networks") or {}
        if not attached:
            return None, []

        network_mode = (info.get("HostConfig") or {}).get("NetworkMode", "")
        if network_mode in attached:
            primary_name = network_mode
        elif network_mode in ("", "default", "bridge") and "bridge" in attached:
            primary_name = "bridge"
        else:
            primary_name = next(iter(attached))

        primary_config: dict[str, Any] | None = None
        additional: list[tuple[str, dict[str, Any]]] = []
        for network_name, settings in attached.items():
            self._client.networks.get(network_name)
            ipam = settings.get("IPAMConfig") or {}
            endpoint_args: dict[str, Any] = {}

            aliases = settings.get("Aliases")
            links = settings.get("Links")
            link_local_ips = ipam.get("LinkLocalIPs")
            if aliases:
                endpoint_args["aliases"] = aliases
            if links:
                endpoint_args["links"] = links
            if ipam.get("IPv4Address"):
                endpoint_args["ipv4_address"] = ipam["IPv4Address"]
            if ipam.get("IPv6Address"):
                endpoint_args["ipv6_address"] = ipam["IPv6Address"]
            if link_local_ips:
                endpoint_args["link_local_ips"] = link_local_ips
            driver_opts = settings.get("DriverOpts")
            if driver_opts:
                endpoint_args["driver_opt"] = driver_opts

            if network_name == primary_name:
                endpoint = self._client.api.create_endpoint_config(**endpoint_args)
                primary_config = self._client.api.create_networking_config(
                    {network_name: endpoint}
                )
            else:
                additional.append((network.id, endpoint_args))

        return primary_config, additional

    async def _recreate_locked(self, name: str, image: str) -> str:
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

        service_name = _strip_slash(info["Name"])

        # Resolve network IDs and construct endpoint settings before teardown.
        # If a network disappeared, fail while the original service is intact.
        networking_config, additional_networks = await asyncio.to_thread(
            self._build_networking_config, info
        )

        await asyncio.to_thread(lambda: container.stop(timeout=10))
        await asyncio.to_thread(lambda: container.remove(force=True))

        def rebuild_with_networks(target_image: str) -> None:
            created = self._client.api.create_container(
                image=target_image,
                command=info["Config"].get("Cmd"),
                entrypoint=info["Config"].get("Entrypoint"),
                environment=info["Config"].get("Env"),
                labels=labels,
                working_dir=info["Config"].get("WorkingDir") or None,
                user=info["Config"].get("User") or None,
                ports=list(info["Config"].get("ExposedPorts") or {}),
                name=service_name,
                host_config=info["HostConfig"],
                networking_config=networking_config,
            )
            for network_id, endpoint_args in additional_networks:
                self._client.api.connect_container_to_network(
                    created["Id"], network_id, **endpoint_args
                )
            self._client.api.start(created["Id"])

        try:
            await asyncio.to_thread(lambda: rebuild_with_networks(image))
        except (DockerException, OSError) as exc:
            # The failure may have come from start rather than create, in which
            # case a container on the new image already holds the name. Left in
            # place it turns the fallback into a name conflict, so the service
            # would stay down on the very path meant to save it.
            await asyncio.to_thread(lambda: self._force_remove_by_name(service_name))

            try:
                await asyncio.to_thread(lambda: rebuild_with_networks(previous_image))
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
