/**
 * State-changing remediation tools.
 *
 * Every tool here is annotated `destructiveHint: true`. TrueForge resolves its
 * `require_approval_for_tools: ["@destructive"]` policy against these
 * annotations, so the annotation is not documentation — it is the mechanism
 * that puts a human in front of the action. A tool moved into this file
 * without the annotation is silently ungated, which is why review treats a
 * missing or dishonest annotation here as a blocking defect.
 *
 * Each tool also requires a free-text `reason`. The approval dialog renders
 * tool arguments, so this puts the agent's justification in front of the person
 * deciding, and the same text lands in the audit log.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { DockerClient, ServiceNotFoundError } from "../docker.js";
import { record } from "../audit.js";

const DESTRUCTIVE = {
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: false,
} as const;

const reasonField = z
  .string()
  .min(10)
  .describe(
    "Why this action is needed, based on evidence you gathered. Shown to the " +
      "human approving it and written to the audit log.",
  );

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }] };
}

function message(error: unknown): string {
  if (error instanceof ServiceNotFoundError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

export function registerWriteTools(server: McpServer, docker: DockerClient): void {
  server.registerTool(
    "restart_service",
    {
      title: "Restart a service",
      description:
        "Restart a service in place, keeping its image and configuration. Use " +
        "this for a service that has crashed, hung, or exhausted a pool of " +
        "connections. In-flight requests will be dropped. Investigate the cause " +
        "before restarting: a restart hides the evidence in memory.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
        reason: reasonField,
      },
      annotations: DESTRUCTIVE,
    },
    async ({ service, reason }) => {
      try {
        await docker.restart(service);
        await record({ action: "restart_service", service, reason, outcome: "succeeded" });
        return text(
          `Restarted ${service}. Confirm recovery with get_service_health before ` +
            `reporting the incident resolved.`,
        );
      } catch (error) {
        const detail = message(error);
        await record({
          action: "restart_service",
          service,
          reason,
          outcome: "failed",
          error: detail,
        });
        return { content: [{ type: "text" as const, text: detail }], isError: true };
      }
    },
  );

  server.registerTool(
    "raise_memory_limit",
    {
      title: "Raise a service memory limit",
      description:
        "Increase the memory ceiling of a running service, applied without a " +
        "restart. Use this only when evidence shows the service was killed for " +
        "exceeding its limit: get_service_health reports oomKilled, or " +
        "get_service_stats shows memory near the limit. Raising the ceiling " +
        "relieves the symptom; a genuine leak will reach the new limit too.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
        limit_mb: z
          .number()
          .int()
          .min(16)
          .max(32768)
          .describe("New memory ceiling in megabytes"),
        reason: reasonField,
      },
      annotations: DESTRUCTIVE,
    },
    async ({ service, limit_mb, reason }) => {
      try {
        const { previousMb } = await docker.setMemoryLimit(service, limit_mb);
        await record({
          action: "raise_memory_limit",
          service,
          reason,
          outcome: "succeeded",
          detail: { previousMb, newMb: limit_mb },
        });
        return text(
          `Memory limit for ${service} set to ${limit_mb} MB ` +
            `(was ${previousMb === null ? "unlimited" : `${previousMb} MB`}). ` +
            `Watch get_service_stats: if usage climbs back to the new ceiling, ` +
            `this is a leak and not an undersized limit.`,
        );
      } catch (error) {
        const detail = message(error);
        await record({
          action: "raise_memory_limit",
          service,
          reason,
          outcome: "failed",
          error: detail,
        });
        return { content: [{ type: "text" as const, text: detail }], isError: true };
      }
    },
  );

  server.registerTool(
    "rollback_deploy",
    {
      title: "Roll a service back to a previous image",
      description:
        "Rebuild a service on a different image tag, keeping its ports, mounts, " +
        "environment and restart policy. Use this when an incident began right " +
        "after a deploy and the logs point at new code. This is the most " +
        "disruptive action available: the container is destroyed and rebuilt, so " +
        "anything written inside it that is not on a mounted volume is lost. The " +
        "target image must already be present locally.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
        image: z
          .string()
          .min(1)
          .describe("Image tag to roll back to, for example checkout-api:1.4.2"),
        reason: reasonField,
      },
      annotations: DESTRUCTIVE,
    },
    async ({ service, image, reason }) => {
      try {
        const { previousImage } = await docker.recreateWithImage(service, image);
        await record({
          action: "rollback_deploy",
          service,
          reason,
          outcome: "succeeded",
          detail: { previousImage, newImage: image },
        });
        return text(
          `Rebuilt ${service} on ${image} (was ${previousImage}). Confirm recovery ` +
            `with get_service_health, and check search_logs for startup errors.`,
        );
      } catch (error) {
        const detail = message(error);
        await record({
          action: "rollback_deploy",
          service,
          reason,
          outcome: "failed",
          error: detail,
        });
        return { content: [{ type: "text" as const, text: detail }], isError: true };
      }
    },
  );
}
