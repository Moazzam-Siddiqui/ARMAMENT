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

def test_rejects_short_container_id_prefix() -> None:
    client = make_client()
    with pytest.raises(ServiceNotFoundError):
        client._resolve_sync("a")


def test_rejects_id_prefix_shorter_than_docker_short_id() -> None:
    client = make_client()
    with pytest.raises(ServiceNotFoundError):
        client._resolve_sync("aaaaaaaaaaa")


def test_accepts_twelve_character_container_id_prefix() -> None:
    client = make_client()
    assert client._resolve_sync("aaaaaaaaaaaa").id == "a" * 64


def test_rejects_ambiguous_container_id_prefix() -> None:
    client = make_client()
    second = FakeContainer()
    second.id = "a" * 12 + "b" * 52
    second.name = "/managed-api-2"
    client._list_managed = lambda: [FakeContainer(), second]
    with pytest.raises(ServiceNotFoundError, match="ambiguous"):
        client._resolve_sync("a" * 12)
