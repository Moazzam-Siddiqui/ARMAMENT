"""Process configuration, read once at startup.

Everything here is validated eagerly so the server refuses to boot in a
misconfigured state rather than failing open on the first agent request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    """The Docker label that marks a container as this agent's responsibility."""

    key: str
    value: str

    def as_filter(self) -> str:
        return f"{self.key}={self.value}"


@dataclass(frozen=True)
class Config:
    port: int
    auth_token: str
    label: Label


def _parse_label(raw: str) -> Label:
    key, separator, value = raw.partition("=")
    if not separator or not key or not value:
        raise ValueError(f'SENTINEL_LABEL must look like "key=value", received "{raw}"')
    return Label(key=key, value=value)


def load_config(env: dict[str, str] | None = None) -> Config:
    source = os.environ if env is None else env

    auth_token = (source.get("SENTINEL_AUTH_TOKEN") or "").strip()
    if not auth_token:
        raise ValueError(
            "SENTINEL_AUTH_TOKEN is required. An unauthenticated server would let "
            "anything on the network restart your containers. Copy .env.example "
            "to .env and set a token."
        )

    raw_port = source.get("PORT") or "8931"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(f'PORT must be an integer, got "{raw_port}"') from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"PORT must be between 1 and 65535, got {port}")

    label_raw = (source.get("SENTINEL_LABEL") or "").strip() or "sentinel.managed=true"

    return Config(port=port, auth_token=auth_token, label=_parse_label(label_raw))
