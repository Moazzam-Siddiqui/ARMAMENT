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
