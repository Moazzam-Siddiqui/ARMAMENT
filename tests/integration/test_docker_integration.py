import asyncio
import uuid

import docker
import pytest

from sentinel_ops.config import load_config
from sentinel_ops.docker_client import DockerClient, ServiceNotFoundError


@pytest.mark.integration
def test_real_docker_lookup_cannot_escape_the_management_label() -> None:
    daemon = docker.from_env()
    suffix = uuid.uuid4().hex[:10]
    managed = None
    unmanaged = None
    scoped = None
    try:
        managed = daemon.containers.run(
            "alpine:3.20",
            "sleep 180",
            detach=True,
            name=f"armament-managed-{suffix}",
            labels={"sentinel.managed": "true"},
        )
        unmanaged = daemon.containers.run(
            "alpine:3.20",
            "sleep 180",
            detach=True,
            name=f"armament-unmanaged-{suffix}",
        )
        scoped = DockerClient(load_config({"SENTINEL_AUTH_TOKEN": "integration-token"}))

        visible_ids = {container.id for container in scoped._list_managed()}
        assert managed.id in visible_ids
        assert unmanaged.id not in visible_ids
        assert scoped._resolve_sync(managed.id).id == managed.id
        with pytest.raises(ServiceNotFoundError):
            scoped._resolve_sync(unmanaged.id)
    finally:
        if scoped is not None:
            scoped._client.close()
        if managed is not None:
            managed.remove(force=True)
        if unmanaged is not None:
            unmanaged.remove(force=True)
        daemon.close()



@pytest.mark.integration
def test_rollback_preserves_multiple_docker_networks_and_aliases() -> None:
    daemon = docker.from_env()
    suffix = uuid.uuid4().hex[:10]
    frontend = daemon.networks.create(f"armament-front-{suffix}", driver="bridge")
    backend = daemon.networks.create(f"armament-back-{suffix}", driver="bridge")
    container_name = f"armament-networked-{suffix}"
    target_image = f"armament-rollback-test:{suffix}"
    managed = None
    scoped = None
    try:
        daemon.images.get("alpine:3.20").tag(target_image)
        host_config = daemon.api.create_host_config(network_mode=frontend.name)
        networking_config = daemon.api.create_networking_config(
            {
                frontend.name: daemon.api.create_endpoint_config(
                    aliases=["frontend-service"]
                )
            }
        )
        created = daemon.api.create_container(
            "alpine:3.20",
            command="sleep 180",
            name=container_name,
            labels={"sentinel.managed": "true"},
            host_config=host_config,
            networking_config=networking_config,
        )
        daemon.api.start(created["Id"])
        managed = daemon.containers.get(created["Id"])
        backend.connect(managed, aliases=["backend-service"])

        scoped = DockerClient(load_config({"SENTINEL_AUTH_TOKEN": "integration-token"}))
        asyncio.run(scoped.recreate_with_image(container_name, target_image))

        replacement = daemon.containers.get(container_name)
        attached = replacement.attrs["NetworkSettings"]["Networks"]
        assert set(attached) == {frontend.name, backend.name}
        assert "frontend-service" in attached[frontend.name]["Aliases"]
        assert "backend-service" in attached[backend.name]["Aliases"]
    finally:
        if scoped is not None:
            scoped._client.close()
        try:
            daemon.containers.get(container_name).remove(force=True)
        except docker.errors.NotFound:
            pass
        frontend.remove()
        backend.remove()
        try:
            daemon.images.remove(target_image, force=True)
        except docker.errors.ImageNotFound:
            pass
        daemon.close()
