# Commercial readiness

## Current release classification

ARMAMENT is a self-hosted developer preview. It is not represented as an
enterprise-ready autonomous SRE platform. The Docker socket gives the service
host-level control, and the TrueForge harness remains a separate dependency.

## Supported first deployment profile

- One trusted operator/team
- One dedicated Linux host or Windows machine running Docker Desktop
- Dockerized workloads explicitly opted in with the configured management label
- Human approval retained for every destructive action
- Groq model provider; Daytona sandbox optional

## Release gates before charging for production use

- [ ] Fresh-machine installation tested on supported Linux and Windows hosts
- [ ] Integration tests against disposable Docker containers
- [ ] Approval workflow tested for restart, memory changes, and rollback
- [ ] Docker socket access and network exposure reviewed independently
- [ ] Audit events persisted, rotated, access-controlled, and recoverable
- [ ] Secrets never included in logs, support bundles, or container output
- [ ] Customer isolation and multi-tenant boundaries designed and tested
- [ ] Backup, upgrade, rollback, and uninstall procedures verified
- [ ] Published support policy, data-flow documentation, and incident process
- [ ] Third-party dependency and license review completed

## Positioning

Start with paid pilots for small engineering teams running Docker Compose
services on a single host. Sell implementation, configuration, and support
before offering a self-serve subscription. Do not market the preview as
unattended production remediation or enterprise SRE.

## Known operational risk

Mounting /var/run/docker.sock effectively grants root-equivalent control of
the Docker host. Restrict host access, firewall ports 8931/8932 to the harness
and trusted operators, and never expose them directly to the public internet.
