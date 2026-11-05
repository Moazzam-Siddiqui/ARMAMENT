# Security policy

## Supported versions

Security fixes are currently applied to the active development branch. There
are no production support guarantees for this developer preview.

## Reporting a vulnerability

Please do not publish exploit details in a public issue or pull request. Use
GitHub's private vulnerability reporting for this repository if it is enabled.
If private reporting is unavailable, contact the repository maintainer through
GitHub and request a private channel before sharing technical details.

Include the affected commit/version, impact, reproduction steps, and any
mitigation you have identified. Do not include live credentials, customer
data, or production secrets.

## Deployment warning

ARMAMENT mounts the Docker Engine socket and can restart, reconfigure, or
recreate opted-in containers. Treat the host as privileged infrastructure.
Restrict network access to ports 8931 and 8932, protect the bearer token, and
test only with disposable workloads until the approval path and isolation have
been validated.
