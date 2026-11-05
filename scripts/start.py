"""Start everything the agent needs, and report what is missing.

Running a demo means four things being up at once: the harness, the MCP server,
the model shim, and at least one managed service. Starting them by hand across
four terminals is easy to get half-right, and a half-started system fails in
ways that look like bugs -- an empty reply from a healthy harness, a connector
that cannot be reached, a turn that dies mid-loop.

This starts the two local processes, checks the rest, and prints one summary.

    python scripts/start.py            # start and stay attached
    python scripts/start.py --check    # report status, start nothing

Ctrl+C stops the processes this script started. Docker containers are left
alone: they outlive it, and stopping the harness mid-turn loses the session.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

HARNESS_URL = "http://localhost:8791/healthz"
SENTINEL_URL = "http://localhost:8931/healthz"
SHIM_URL = "http://localhost:8932/healthz"

# Long enough for uvicorn to bind, short enough that a real failure is obvious.
STARTUP_TIMEOUT_SECONDS = 25.0
POLL_SECONDS = 0.5


@dataclass
class Service:
    name: str
    url: str
    command: list[str] | None
    hint: str


SERVICES = [
    Service(
        name="TrueForge harness",
        url=HARNESS_URL,
        # Not ours to start: it lives in a separate checkout with its own compose
        # file, and guessing where would be worse than saying so.
        command=None,
        hint="cd into your trueforge checkout and run: docker compose up -d",
    ),
    Service(
        name="sentinel-ops",
        url=SENTINEL_URL,
        command=[sys.executable, "-m", "sentinel_ops"],
        hint="python -m sentinel_ops",
    ),
    Service(
        name="groq-shim",
        url=SHIM_URL,
        command=[sys.executable, str(ROOT / "scripts" / "groq_shim.py")],
        hint="python scripts/groq_shim.py",
    ),
]


def is_up(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def managed_services() -> list[str] | None:
    """Names of containers the agent can see, or None if Docker is unreachable."""
    try:
        result = subprocess.run(
            [
                "docker", "ps",
                "--filter", "label=sentinel.managed=true",
                "--format", "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def missing_keys() -> list[str]:
    required = ("SENTINEL_AUTH_TOKEN", "GROQ_API_KEY")
    return [key for key in required if not (os.environ.get(key) or "").strip()]


def wait_until_up(service: Service, process: subprocess.Popen) -> bool:
    """Poll until the service answers, or it exits, or we run out of patience."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_up(service.url):
            return True
        time.sleep(POLL_SECONDS)
    return False


def report(started: list[str]) -> int:
    """Print the state of everything and return an exit code."""
    print()
    print("  service            status")
    print("  " + "-" * 46)

    problems = []
    for service in SERVICES:
        up = is_up(service.url)
        note = "" if up else "  <- " + service.hint
        print(f"  {service.name:<18} {'up' if up else 'DOWN'}{note}")
        if not up:
            problems.append(service.name)

    containers = managed_services()
    if containers is None:
        print(f"  {'managed services':<18} unknown  <- Docker is not reachable")
        problems.append("docker")
    elif containers:
        print(f"  {'managed services':<18} {', '.join(containers)}")
    else:
        print(
            f"  {'managed services':<18} none     <- nothing is labelled "
            f"sentinel.managed=true, so the agent will see an empty list"
        )

    print()
    if started:
        print(f"Started by this script: {', '.join(started)}")
    if problems:
        print("Not ready. Fix the items marked above, then re-run.")
        return 1

    print("Ready. Open http://localhost:8791 and start a session with 'sentinel'.")
    return 0


def main() -> int:
    # The child processes write straight to this terminal while our own output
    # sits in a buffer, so without this the summary appears after everything it
    # was meant to introduce -- or not at all when the output is redirected.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is running without starting anything",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)

    absent = missing_keys()
    if absent and not args.check:
        print(f"Missing from .env: {', '.join(absent)}")
        print("See .env.example. Nothing was started.")
        return 1

    if args.check:
        return report(started=[])

    processes: list[tuple[str, subprocess.Popen]] = []
    started: list[str] = []

    try:
        for service in SERVICES:
            if service.command is None:
                continue
            if is_up(service.url):
                print(f"{service.name}: already running")
                continue

            print(f"{service.name}: starting")
            process = subprocess.Popen(service.command, cwd=ROOT)
            processes.append((service.name, process))

            if wait_until_up(service, process):
                started.append(service.name)
            else:
                # Its own output is already on this terminal, so the reason is
                # visible above rather than swallowed and re-reported here.
                print(f"{service.name}: failed to come up")

        code = report(started)
        if not processes:
            return code

        print("\nCtrl+C to stop the processes this script started.")
        while True:
            for name, process in processes:
                if process.poll() is not None:
                    print(f"\n{name} exited with code {process.returncode}")
                    return 1
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nstopping")
        return 0
    finally:
        for name, process in processes:
            if process.poll() is None:
                process.terminate()
        for name, process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
