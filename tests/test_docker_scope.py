import pytest

from sentinel_ops.config import Label
from sentinel_ops.docker_client import DockerClient, ServiceNotFoundError


class FakeContainer:
    id = "a" * 64
    name = "/managed-api"


def make_client() -> DockerClient:
    client = DockerClient.__new__(DockerClient)
    client._label = Label("sentinel.managed", "true")
    client._list_managed = lambda: [FakeContainer()]
    return client


def test_resolves_managed_container_by_name_or_id_prefix() -> None:
    client = make_client()
    assert client._resolve_sync("managed-api").id == "a" * 64
    assert client._resolve_sync("aaaaaaaaaaaa").name == "/managed-api"


def test_refuses_container_outside_managed_listing() -> None:
    client = make_client()
    with pytest.raises(ServiceNotFoundError):
        client._resolve_sync("unmanaged-api")
    with pytest.raises(ServiceNotFoundError):
        client._resolve_sync("b" * 64)
