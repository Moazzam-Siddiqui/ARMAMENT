# Best Practices

Repo-specific rules for code review.

## Safety — treat violations as blocking

- Never execute model-generated code outside the sandbox. Any new execution path
  must route through the configured sandbox provider.
- Every irreversible action (write, delete, restart, deploy, scale) must be
  gated behind an explicit human approval checkpoint before it fires.
- Approval gates must fail closed. If approval state is missing, unknown, or the
  request times out, do not perform the action.
- Destructive MCP tools must carry accurate annotations. The harness derives its
  approval policy from them, so a mislabelled tool silently disables the gate.
- Never log, echo, or return secrets, API keys, or tokens. Secrets stay in the
  harness and are never passed into sandboxed code or tool arguments.

## Tool design

- Tool definitions need explicit input schemas and human-readable descriptions;
  the model relies on these to call tools correctly.
- Tool handlers must validate their inputs rather than trusting model output.
- Errors returned to the model should be actionable text, not raw stack traces.
- Keep the tool surface small and purposeful — prefer a few well-described tools
  over many overlapping ones.

## General

- No secrets, keys, or `.env` contents committed. `.env.example` only.
- Prefer explicit types over `any`; no silently swallowed exceptions.
- Public functions and tools carry doc comments explaining intent, not mechanics.
