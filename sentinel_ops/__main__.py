"""Entry point.

Validates configuration and reachability before listening, so a broken setup
surfaces at startup instead of midway through an incident.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from dotenv import load_dotenv

from .config import Config, load_config
from .docker_client import DockerClient
from .server import create_app

log = logging.getLogger("sentinel-ops")


async def _check_docker(docker: DockerClient) -> None:
    try:
        await docker.ping()
    except Exception as exc:  # noqa: BLE001 - reported as a startup failure
        raise RuntimeError(
            f"Cannot reach the Docker daemon: {exc}. Start Docker, or set "
            f"DOCKER_HOST if it listens somewhere non-standard."
        ) from exc


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stdout,
    )

    # Loaded before load_config so a .env file works as the README describes.
    # Real environment variables win, which keeps deployments overridable.
    load_dotenv(override=False)

    try:
        config: Config = load_config()
        docker = DockerClient(config)
        asyncio.run(_check_docker(docker))
    except (ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1

    app = create_app(config, docker)

    log.info("listening on http://localhost:%d/mcp", config.port)
    log.info(
        "managing containers labelled %s=%s", config.label.key, config.label.value
    )

    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
