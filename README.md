# ARMAMENT

**Approval-gated incident response for Dockerized services.**

ARMAMENT investigates service health, logs, and resource usage, explains the
evidence, and can propose a remediation. Restart, memory-limit changes, and
image rollback are destructive operations and must be approved by a human in
the TrueForge harness before execution.

> **Release status: self-hosted developer preview.** This release packages the
> ARMAMENT MCP server and Groq compatibility shim with Docker Compose. TrueForge
> remains a separate required harness. Do not connect this preview to production
> infrastructure before completing the security and end-to-end validation
> checklist in docs/COMMERCIAL_READINESS.md.

## Capabilities

- Discovers only containers carrying the configured management label
  (default: sentinel.managed=true).
- Reads service health, logs, restart history, and resource statistics.
- Offers restart, memory-limit increase, and image rollback tools.
- Marks every state-changing tool as destructive for harness approval.
- Records state-changing attempts in a persistent, size-bounded JSONL audit log.

## Quick start

Requirements: Docker Engine/Desktop with Compose v2, Python 3.11+, a Groq API
key, and a separate TrueForge installation. Daytona is optional.

```bash
git clone https://github.com/Moazzam-Siddiqui/ARMAMENT
cd ARMAMENT
python scripts/bootstrap.py
```

The bootstrap creates a virtual environment, installs the Python package,
prompts for credentials without echoing them, generates a random MCP bearer
token, creates a private .env without overwriting an existing one, builds the
containers, and waits for both local health endpoints.

Then install/start TrueForge separately and provision the agent using the
created environment:

```bash
# Windows PowerShell
.\.venv\Scripts\python.exe scripts/provision.py
# macOS/Linux
.venv/bin/python scripts/provision.py
```

Open http://localhost:8791 and start a session with the sentinel agent.
The read-only ARMAMENT operations dashboard is at
http://localhost:8931/dashboard. Enter the SENTINEL_AUTH_TOKEN from your
private .env to view managed services and recent remediation audit events.
Approvals and remediation actions remain in the TrueForge UI.

Follow the full docs/setup.md, docs/architecture.md, docs/tools.md, and
docs/troubleshooting.md guides.

## Safety boundaries

- Only containers with the configured management label can be listed or
  resolved by name/ID. Label only workloads you explicitly authorize.
- The Docker socket is effectively root-equivalent access to the host. Run on a
  dedicated trusted machine and protect the Docker daemon.
- MCP and Groq-shim ports are published for the separate TrueForge container.
  Restrict them with the host firewall to trusted sources. Never port-forward
  them or expose them directly to the public internet.
- The MCP endpoint requires a generated bearer token. The Groq shim separately
  requires the provider's bearer credential and enforces a 2 MiB request-body
  limit plus a 16-request concurrency ceiling. The Groq API key is supplied to
  the shim through its Compose environment and remains a secret.
- Keep .env private. It is used by the provisioning script and is gitignored.
- Approval is enforced by TrueForge using destructive-tool annotations. Verify
  that approval prompts work before enabling any remediation.
- The audit log rotates at 10 MiB with five backups. It is local operational
  evidence, not tamper-proof or a substitute for centralized audit storage.
- No independent security audit or production end-to-end validation is claimed.

## Operations

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

The named armament-audit volume survives container recreation. Back it up
according to your organization's retention policy.

## Development

```bash
python -m pip install -e ".[dev]"
python -m compileall -q sentinel_ops scripts tests
python -m pytest -q
ruff check .
```

## License

MIT. See LICENSE.
