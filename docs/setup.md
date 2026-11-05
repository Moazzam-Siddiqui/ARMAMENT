# Setup

This guide installs ARMAMENT's local services from a fresh clone. The TrueForge
agent harness remains a separate dependency and must be installed separately.

## Requirements

- Docker Engine/Desktop with Docker Compose v2
- Python 3.11+ (for the provisioning script)
- Groq API key
- TrueForge harness, running on the same host
- Optional Daytona API key if you want sandboxed code execution

## 1. Install ARMAMENT

```bash
git clone https://github.com/Moazzam-Siddiqui/ARMAMENT
cd ARMAMENT
python scripts/bootstrap.py
```

The setup script verifies Docker Compose, creates a local Python virtual
environment and installs the provisioning package, prompts for credentials
without echoing them, generates a high-entropy MCP bearer token, writes .env
with restrictive permissions where supported, builds the two ARMAMENT
containers, and waits for their health endpoints. It refuses to overwrite an
existing .env.

## 2. Network and Docker permissions

The compose stack publishes ports 8931 (MCP) and 8932 (Groq shim) on the host
because the TrueForge harness runs in its own container and reaches these
services through host.docker.internal. The default bootstrap binds to all host
interfaces. Restrict both ports using your host firewall to the TrueForge
container/host and trusted operator machines. Do not expose them to the public
internet.

The sentinel-ops container mounts the Docker socket. Access to that socket is
effectively root-equivalent control of the Docker host. Use a dedicated host,
label only the workloads ARMAMENT is allowed to manage, and do not share this
machine with untrusted workloads.

On Linux Docker Engine, ensure the TrueForge server container has this mapping
so host.docker.internal resolves:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Docker Desktop normally provides host.docker.internal automatically.

## 3. Start TrueForge

Install TrueForge in a separate checkout, following its upstream instructions.
Start its server and verify that the host can reach:

```bash
curl http://localhost:8791/healthz
```

If TrueForge is containerized, its server must listen on 0.0.0.0 inside the
container and publish its configured host port. Keep the TrueForge checkout
outside the ARMAMENT repository.

## 4. Provision the agent

```bash
# Windows PowerShell
.\.venv\Scripts\python.exe scripts/provision.py
# macOS/Linux
.venv/bin/python scripts/provision.py
```

The provisioning script reads GROQ_API_KEY and optional DAYTONA_API_KEY from
.env, then registers the Groq-compatible model endpoint, authenticated MCP
connector, optional sandbox provider, and sentinel agent with TrueForge. It is
safe to re-run after changing configuration.

## 5. Check readiness

```bash
docker compose ps
curl http://localhost:8931/healthz
curl http://localhost:8932/healthz
curl http://localhost:8791/healthz
```

These endpoints prove process liveness, not that a full model/tool/approval
workflow works. Open http://localhost:8931/dashboard and enter the
SENTINEL_AUTH_TOKEN from your private .env to inspect the managed-service
inventory and recent remediation audit. The dashboard is read-only; approval
prompts and remediation remain in the TrueForge UI. Complete a disposable-
container smoke test before connecting any real workload.

## 6. Run a disposable test workload

```bash
docker run -d --label sentinel.managed=true --name armament-demo \
  --memory 256m alpine:3.20 \
  sh -c 'echo "ERROR: demo incident for ARMAMENT"; sleep 3600'
```

Open the TrueForge UI, start a session with the sentinel agent, and ask it to
investigate armament-demo. Inspect the evidence and confirm the harness pauses
for human approval before any restart, memory change, or rollback.

Remove the demo when finished:

```bash
docker rm -f armament-demo
```

## 7. Operations

```bash
docker compose logs -f
docker compose restart
docker compose down
```

The named armament-audit volume persists the local JSONL audit log. It rotates
at 10 MiB and retains five backups. This is not tamper-proof centralized audit
storage.

## 8. Upgrade and uninstall

To update to the latest branch revision:

```bash
git pull
docker compose up --build -d
```

Re-run provisioning with the virtual-environment Python if the model, tools, or
configuration changed:

```bash
# Windows PowerShell
.venv\\Scripts\\python.exe scripts/provision.py
# macOS/Linux
.venv/bin/python scripts/provision.py
```

To stop ARMAMENT while preserving its audit volume:

```bash
docker compose down
```

To remove the containers and permanently delete the named audit volume:

```bash
docker compose down -v
```

Back up the audit volume before deleting it if those records are needed.

## Troubleshooting

- Docker unavailable: start Docker Desktop/Engine and run docker compose version.
- MCP unreachable from TrueForge: check host.docker.internal resolution, host
  firewall rules, and that port 8931 is published.
- Groq provider unreachable: check port 8932, the model provider base URL, and
  the Groq credential stored in the host-side .env.
- No services listed: ensure the target containers carry the exact configured
  label (default sentinel.managed=true).
- Approval not shown: stop testing remediation and inspect TrueForge's
  destructive-tool approval configuration before proceeding.
