# Crvt5.2 release plan

ARMAMENT remains a developer preview until the following checks are completed.

- Verify a clean-machine install from a fresh clone.
- Run automated tests against a disposable Docker daemon and labelled test containers.
- Exercise approval-required restart, memory-limit, and rollback paths end to end.
- Review the Docker socket trust boundary and network exposure.
- Validate secret handling, audit retention, and recovery after process crashes.
- Provide a customer setup flow, operational dashboard, and upgrade path.
- Document support boundaries, data handling, and licensing for paid deployments.

Initial customer profile: small engineering teams operating Docker Compose services on a single host. Do not describe this preview as autonomous enterprise SRE or deploy it against production workloads without a separate validation and security review.
