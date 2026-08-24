/**
 * Read-only investigation tools.
 *
 * Every tool here is annotated `readOnlyHint: true`. The harness derives its
 * approval policy from these annotations, so the labels must stay honest: a
 * tool that mutates anything does not belong in this file.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { DockerClient, ServiceNotFoundError } from "../docker.js";

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
} as const;

/** Renders a value as a text tool result. */
function text(value: unknown) {
  const body = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return { content: [{ type: "text" as const, text: body }] };
}

/** Renders an error the model can act on, rather than a stack trace. */
function failure(error: unknown) {
  const message =
    error instanceof ServiceNotFoundError
      ? error.message
      : error instanceof Error
        ? error.message
        : String(error);
  return { content: [{ type: "text" as const, text: message }], isError: true };
}

export function registerReadTools(server: McpServer, docker: DockerClient): void {
  server.registerTool(
    "list_services",
    {
      title: "List services",
      description:
        "List every managed service with its current state, health, uptime and " +
        "restart count. Start here when investigating an incident: it shows which " +
        "services exist and which are unhealthy.",
      inputSchema: {},
      annotations: READ_ONLY,
    },
    async () => {
      try {
        const services = await docker.listServices();
        if (services.length === 0) {
          return text(
            "No managed services found. Services must carry the manager's label " +
              "to be visible to this agent.",
          );
        }
        return text(services);
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "get_service_health",
    {
      title: "Get service health",
      description:
        "Detailed health for one service: state, exit code, health-check history, " +
        "restart count, and whether it was killed for running out of memory. Use " +
        "this after list_services to understand why a specific service is failing.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
      },
      annotations: READ_ONLY,
    },
    async ({ service }) => {
      try {
        const info = await docker.inspect(service);
        return text({
          name: info.Name.replace(/^\//, ""),
          image: info.Config.Image,
          state: info.State.Status,
          running: info.State.Running,
          exitCode: info.State.ExitCode,
          oomKilled: info.State.OOMKilled,
          restartCount: info.RestartCount,
          startedAt: info.State.StartedAt,
          finishedAt: info.State.FinishedAt,
          error: info.State.Error || null,
          health: info.State.Health
            ? {
                status: info.State.Health.Status,
                failingStreak: info.State.Health.FailingStreak,
                // Only the last few probes matter; the full log is long and repetitive.
                recentChecks: info.State.Health.Log?.slice(-5).map((entry) => ({
                  start: entry.Start,
                  exitCode: entry.ExitCode,
                  output: entry.Output?.slice(0, 500),
                })),
              }
            : null,
        });
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "search_logs",
    {
      title: "Search service logs",
      description:
        "Fetch recent log lines for a service, optionally filtered by a case-" +
        "insensitive substring and a time window. Use this to find the error " +
        "behind a failing health check.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
        contains: z
          .string()
          .optional()
          .describe("Only return lines containing this text (case-insensitive)"),
        tail: z
          .number()
          .int()
          .min(1)
          .max(1000)
          .default(200)
          .describe("How many recent lines to scan"),
        since_seconds: z
          .number()
          .int()
          .min(1)
          .optional()
          .describe("Only scan lines from the last N seconds"),
      },
      annotations: READ_ONLY,
    },
    async ({ service, contains, tail, since_seconds }) => {
      try {
        const raw = await docker.logs(service, tail, since_seconds);
        const lines = raw.split("\n").filter((line) => line.trim().length > 0);
        const matched = contains
          ? lines.filter((line) => line.toLowerCase().includes(contains.toLowerCase()))
          : lines;

        if (matched.length === 0) {
          return text(
            contains
              ? `No lines containing "${contains}" in the last ${tail} lines of ${service}.`
              : `No log output for ${service}.`,
          );
        }
        return text(matched.join("\n"));
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "get_service_stats",
    {
      title: "Get service resource usage",
      description:
        "Current CPU and memory usage for a service. Use this to confirm or rule " +
        "out resource exhaustion as the cause of an incident.",
      inputSchema: {
        service: z.string().min(1).describe("Name of the managed service"),
      },
      annotations: READ_ONLY,
    },
    async ({ service }) => {
      try {
        return text(await docker.stats(service));
      } catch (error) {
        return failure(error);
      }
    },
  );
}
