/**
 * Process configuration, read once at startup.
 *
 * Everything here is validated eagerly so the server refuses to boot in a
 * misconfigured state rather than failing open on the first agent request.
 */

export interface Config {
  /** Port the MCP endpoint listens on. */
  port: number;
  /** Shared secret required on every request as a bearer token. */
  authToken: string;
  /** Docker label restricting which containers the agent can see or touch. */
  label: { key: string; value: string };
}

function parseLabel(raw: string): { key: string; value: string } {
  const separator = raw.indexOf("=");
  if (separator < 1 || separator === raw.length - 1) {
    throw new Error(
      `SENTINEL_LABEL must look like "key=value", received "${raw}"`,
    );
  }
  return {
    key: raw.slice(0, separator),
    value: raw.slice(separator + 1),
  };
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const authToken = env.SENTINEL_AUTH_TOKEN?.trim();
  if (!authToken) {
    throw new Error(
      "SENTINEL_AUTH_TOKEN is required. An unauthenticated server would let " +
        "anything on the network restart your containers. Copy .env.example " +
        "to .env and set a token.",
    );
  }

  const port = Number(env.PORT ?? 8931);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`PORT must be an integer between 1 and 65535, got "${env.PORT}"`);
  }

  return {
    port,
    authToken,
    label: parseLabel(env.SENTINEL_LABEL?.trim() || "sentinel.managed=true"),
  };
}
