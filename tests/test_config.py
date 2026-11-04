import pytest

from sentinel_ops.config import load_config


def valid_env(**overrides: str) -> dict[str, str]:
    env = {"SENTINEL_AUTH_TOKEN": "test-token"}
    env.update(overrides)
    return env


def test_loads_safe_defaults() -> None:
    config = load_config(valid_env())

    assert config.host == "127.0.0.1"
    assert config.port == 8931
    assert config.label.key == "sentinel.managed"
    assert config.label.value == "true"


def test_requires_authentication_token() -> None:
    with pytest.raises(ValueError, match="SENTINEL_AUTH_TOKEN is required"):
        load_config({})


@pytest.mark.parametrize("port", ["0", "65536", "-1", "not-a-port"])
def test_rejects_invalid_ports(port: str) -> None:
    with pytest.raises(ValueError, match="PORT"):
        load_config(valid_env(PORT=port))


def test_parses_custom_container_label() -> None:
    config = load_config(valid_env(SENTINEL_LABEL="armament.customer=acme"))

    assert config.label.key == "armament.customer"
    assert config.label.value == "acme"
    assert config.label.as_filter() == "armament.customer=acme"


@pytest.mark.parametrize("label", ["", "missing-value", "=value", "key="])
def test_rejects_malformed_container_labels(label: str) -> None:
    with pytest.raises(ValueError, match="SENTINEL_LABEL"):
        load_config(valid_env(SENTINEL_LABEL=label))


def test_allows_harness_host_with_and_without_port() -> None:
    config = load_config(valid_env(PORT="9000"))

    assert "host.docker.internal" in config.allowed_hosts
    assert "host.docker.internal:9000" in config.allowed_hosts
    assert "localhost:9000" in config.allowed_hosts


def test_deduplicates_allowed_hosts() -> None:
    config = load_config(
        valid_env(SENTINEL_ALLOWED_HOSTS="localhost, staging.internal")
    )

    assert config.allowed_hosts.count("localhost") == 1
    assert "staging.internal" in config.allowed_hosts
    assert "staging.internal:8931" in config.allowed_hosts
