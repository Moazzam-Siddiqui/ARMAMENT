"""Configure a TrueForge harness to run the sentinel agent.

Registers the model provider, the sentinel-ops connector, and the agent itself
against a running harness. Written as a script rather than documented as a
click-path so the configuration is reviewable, diffable, and reproducible on a
fresh machine.

Safe to re-run: every resource is created or replaced by name.

    python scripts/provision.py

Reads from .env (or the real environment, which wins):

    SENTINEL_AUTH_TOKEN   bearer token the harness sends to sentinel-ops
    GROQ_API_KEY          model provider credential
    TRUEFORGE_URL         harness base URL      (default http://localhost:8791)
    SENTINEL_MCP_URL      how the harness reaches sentinel-ops
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

# The harness runs in a container while sentinel-ops runs on the host, so
# "localhost" inside the container is the container itself. host.docker.internal
# is the host as seen from inside.
DEFAULT_MCP_URL = "http://host.docker.internal:8931/mcp"
DEFAULT_TRUEFORGE_URL = "http://localhost:8791"

PROVIDER_NAME = "groq"
MODEL_NAME = "gpt-oss-120b"
CONNECTOR_NAME = "sentinel-ops"
AGENT_NAME = "sentinel"

INSTRUCTIONS = """\
You are an on-call engineer responsible for a set of containerised services.

Your job is to find out what is actually wrong and say so plainly. You have
read-only tools that cost nothing to use and destructive tools that change
running infrastructure.

How to work:

1. Investigate before you act. Start with list_services to see what is
   unhealthy. Follow the evidence with get_service_health, search_logs and
   get_service_stats. Never propose a fix you cannot point at evidence for.

2. Say what you found before you propose anything. State the symptom, the
   evidence, and your reading of the cause. If the evidence is ambiguous, say
   that instead of guessing.

3. Match the action to the cause, not to the symptom:
   - A crashed, hung or connection-exhausted service needs restart_service.
   - A service killed at its memory ceiling (oomKilled, or usage sitting at the
     limit) needs raise_memory_limit.
   - An incident that began immediately after a deploy, with logs pointing at
     new code, needs rollback_deploy.

4. Restarting destroys evidence held in memory. Gather what you need from the
   logs first.

5. Every destructive action needs human approval, and your stated reason is
   shown to the person deciding. Write it for them: what you saw, and why this
   action follows from it. Not "restarting to fix the error".

6. After an approved action, verify. Re-check health, and read the logs again
   for startup errors. Do not report an incident resolved on the strength of
   the action having been accepted.

7. If a destructive action is refused, or fails for a stated reason, do not
   retry it unchanged. Read the reason, and either gather more evidence or
   explain why you disagree.

You only see services that are explicitly placed in your care. If a service is
not in list_services, it is not yours to touch, and you should say so rather
than looking for another way to reach it.
"""


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach the harness at {url}: {exc.reason}\n"
            f"Start it with: docker compose up -d  (in the trueforge checkout)"
        ) from exc


def _upsert(base: str, collection: str, name: str, manifest: dict) -> None:
    """Create the resource, replacing any existing one with the same name.

    The API exposes both a create and a create-or-replace form; this tries the
    idempotent one first so re-running the script is not an error.
    """
    body = {"manifest": manifest}

    status, text = _request("PUT", f"{base}/{collection}/{name}", body)
    if status in (200, 201, 204):
        print(f"  ok    {collection}/{name}")
        return

    # Older builds expose creation only as a POST against the collection.
    if status in (404, 405):
        status, text = _request("POST", f"{base}/{collection}", {"name": name, **body})
        if status in (200, 201, 204):
            print(f"  ok    {collection}/{name} (created)")
            return

    print(f"  FAIL  {collection}/{name} -> HTTP {status}")
    print(f"        {text[:400]}")
    raise SystemExit(1)


def main() -> int:
    load_dotenv(override=False)

    sentinel_token = (os.environ.get("SENTINEL_AUTH_TOKEN") or "").strip()
    groq_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not sentinel_token:
        raise SystemExit("SENTINEL_AUTH_TOKEN is not set; see .env.example")
    if not groq_key:
        raise SystemExit("GROQ_API_KEY is not set; see .env.example")

    base = (os.environ.get("TRUEFORGE_URL") or DEFAULT_TRUEFORGE_URL).rstrip("/")
    mcp_url = os.environ.get("SENTINEL_MCP_URL") or DEFAULT_MCP_URL
    api = f"{base}/api/v1"

    print(f"harness      {base}")
    print(f"sentinel-ops {mcp_url}")
    print()

    print("model provider")
    _upsert(
        f"{api}/settings",
        "model-providers",
        PROVIDER_NAME,
        {
            "type": "custom",
            "name": PROVIDER_NAME,
            # Groq speaks the OpenAI wire format, so it registers as a custom
            # endpoint rather than needing a dedicated provider type.
            "base_url": "https://api.groq.com/openai/v1",
            "auth": {"api_key": groq_key},
            "models": [
                {
                    "name": MODEL_NAME,
                    "model_id": "openai/gpt-oss-120b",
                    "properties": {
                        "context_length": 131072,
                        "max_output_tokens": 32768,
                    },
                }
            ],
        },
    )

    print("connector")
    _upsert(
        f"{api}/settings",
        "mcp-servers",
        CONNECTOR_NAME,
        {
            "type": "remote",
            "name": CONNECTOR_NAME,
            "url": mcp_url,
            "description": (
                "Live state and remediation for containerised services: health, "
                "logs, resource usage, restart, memory limit, and rollback."
            ),
            "auth": {
                "type": "header",
                "headers": {"Authorization": f"Bearer {sentinel_token}"},
            },
        },
    )

    print("agent")
    _upsert(
        api,
        "agents",
        AGENT_NAME,
        {
            "model": {
                "name": f"{PROVIDER_NAME}/{MODEL_NAME}",
                "params": {
                    # Low but not zero: incident triage should be repeatable,
                    # not creative.
                    "temperature": 0.2,
                    "parallel_tool_calls": True,
                },
            },
            "instructions": INSTRUCTIONS,
            "mcp_servers": [
                {
                    "name": CONNECTOR_NAME,
                    "enable_tools": ["@all"],
                    # The gate that makes this agent safe to run. It resolves
                    # against the destructive annotations the connector reports,
                    # so the connector's own honesty is what arms it.
                    "require_approval_for_tools": ["@destructive"],
                    "preload": True,
                }
            ],
            "config": {
                # No sandbox provider is configured yet; enabling it without one
                # fails at run time rather than at startup.
                "sandbox": {"enabled": False},
                "ask_user_questions": {"enabled": True},
                # A single incident should not fan out into parallel agents
                # touching the same services.
                "dynamic_sub_agents": {"enabled": False},
                "iteration_limit": 40,
            },
        },
    )

    print()
    print(f"Done. Open {base} and start a session with the '{AGENT_NAME}' agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
