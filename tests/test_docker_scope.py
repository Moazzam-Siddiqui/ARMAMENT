from types import SimpleNamespace

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



def test_rebuild_network_config_preserves_all_attachments_and_aliases() -> None:
    client = DockerClient.__new__(DockerClient)
    networks = {
        "frontend": SimpleNamespace(id="network-frontend"),
        "backend": SimpleNamespace(id="network-backend"),
    }
    client._client = SimpleNamespace(
        networks=SimpleNamespace(get=lambda name: networks[name]),
        api=SimpleNamespace(
            create_endpoint_config=lambda **kwargs: kwargs,
            create_networking_config=lambda endpoints: endpoints,
        ),
    )

    config = client._build_networking_config(
        {
            "NetworkSettings": {
                "Networks": {
                    "frontend": {
                        "Aliases": ["managed-api", "frontend-alias"],
                        "IPAMConfig": {"IPv4Address": "172.20.0.10"},
                    },
                    "backend": {
                        "Aliases": ["managed-api", "backend-alias"],
                        "Links": ["db:database"],
                        "IPAMConfig": {
                            "IPv6Address": "fd00::10",
                            "LinkLocalIPs": ["169.254.10.10"],
                        },
                        "DriverOpts": {"com.example.option": "value"},
                    },
                }
            }
        }
    )

    assert config == (
        {
            "frontend": {
                "aliases": ["managed-api", "frontend-alias"],
                "ipv4_address": "172.20.0.10",
            }
        },
        [
            (
                "network-backend",
                {
                    "aliases": ["managed-api", "backend-alias"],
                    "links": ["db:database"],
                    "ipv6_address": "fd00::10",
                    "link_local_ips": ["169.254.10.10"],
                    "driver_opt": {"com.example.option": "value"},
                },
            )
        ],
    )


def test_rebuild_network_config_is_empty_without_network_attachments() -> None:
    client = DockerClient.__new__(DockerClient)
    client._client = SimpleNamespace()
    assert client._build_networking_config({"NetworkSettings": {"Networks": {}}}) == (
        None,
        [],
    )


def test_container_name_resolution_is_case_sensitive() -> None:
    client = make_client()
    second = FakeContainer()
    second.id = "b" * 64
    second.name = "/Managed-API"
    client._list_managed = lambda: [FakeContainer(), second]

    assert client._resolve_sync("managed-api").id == "a" * 64
    assert client._resolve_sync("Managed-API").id == "b" * 64


def test_service_name_and_container_id_share_the_same_lock() -> None:
    client = make_client()
    by_name = client._lock_for("managed-api")
    by_id = client._lock_for("managed-api")
    assert by_name is by_id
