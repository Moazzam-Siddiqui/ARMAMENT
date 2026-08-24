/**
 * Append-only record of every state-changing action.
 *
 * The harness gates these actions on human approval, but the gate lives outside
 * this process and leaves no trace here. This log is the server's own account
 * of what it was asked to do and what happened, written from inside the code
 * path that performs the work so nothing can act without being recorded.
 */

import { appendFile } from "node:fs/promises";
import { resolve } from "node:path";

export interface AuditEntry {
  action: string;
  service: string;
  /** The agent's stated justification, carried through from the tool call. */
  reason: string;
  outcome: "succeeded" | "failed";
  detail?: Record<string, unknown>;
  error?: string;
}

const LOG_PATH = resolve(process.cwd(), "sentinel-audit.log");

/**
 * Writes one JSON line. Logging failures are reported but never thrown: losing
 * an audit line must not turn a successful remediation into a reported failure,
 * which would push the agent into retrying an action that already happened.
 */
export async function record(entry: AuditEntry): Promise<void> {
  const line = JSON.stringify({ at: new Date().toISOString(), ...entry });
  try {
    await appendFile(LOG_PATH, `${line}\n`, "utf8");
  } catch (error) {
    console.error("[sentinel-ops] audit write failed:", error);
  }
  console.log(`[sentinel-ops] ${line}`);
}
