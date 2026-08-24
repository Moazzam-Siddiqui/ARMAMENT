/**
 * Thin wrapper over the Docker Engine API.
 *
 * Every lookup here is scoped to the configured label. Containers without it
 * are not merely filtered from listings — they cannot be resolved by name at
 * all, so no tool can reach them even if the model asks by exact ID. This is
 * the blast-radius boundary for the whole server.
 */

import Docker from "dockerode";
import type { Config } from "./config.js";

export interface ServiceSummary {
  name: string;
  id: string;
  image: string;
  state: string;
  status: string;
  health: string;
  startedAt: string | null;
  restartCount: number;
}

export interface ServiceStats {
  name: string;
  cpuPercent: number | null;
  memoryUsedMb: number | null;
  memoryLimitMb: number | null;
  memoryPercent: number | null;
}

/** Raised when a service is absent, or present but outside the managed scope. */
export class ServiceNotFoundError extends Error {
  constructor(name: string) {
    super(
      `No managed service named "${name}". It either does not exist or is not ` +
        `labelled as managed, which puts it outside this agent's reach.`,
    );
    this.name = "ServiceNotFoundError";
  }
}

export class DockerClient {
  readonly #docker: Docker;
  readonly #label: string;

  constructor(config: Config) {
    this.#docker = new Docker();
    this.#label = `${config.label.key}=${config.label.value}`;
  }

  /** Verifies the daemon is reachable, so startup fails loudly rather than per-request. */
  async ping(): Promise<void> {
    await this.#docker.ping();
  }

  async listServices(): Promise<ServiceSummary[]> {
    const containers = await this.#docker.listContainers({
      all: true,
      filters: { label: [this.#label] },
    });

    const summaries = await Promise.all(
      containers.map(async (container) => {
        const details = await this.#docker
          .getContainer(container.Id)
          .inspect()
          .catch(() => null);

        return {
          name: stripLeadingSlash(container.Names[0] ?? container.Id),
          id: container.Id.slice(0, 12),
          image: container.Image,
          state: container.State,
          status: container.Status,
          health: details?.State.Health?.Status ?? "none",
          startedAt: details?.State.StartedAt ?? null,
          restartCount: details?.RestartCount ?? 0,
        };
      }),
    );

    return summaries.sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Resolves a service name to a container, refusing anything unlabelled.
   * All write paths must go through this rather than addressing Docker directly.
   */
  async resolve(name: string): Promise<Docker.Container> {
    const containers = await this.#docker.listContainers({
      all: true,
      filters: { label: [this.#label] },
    });

    const wanted = stripLeadingSlash(name).toLowerCase();
    const match = containers.find(
      (container) =>
        container.Id === name ||
        container.Id.startsWith(wanted) ||
        container.Names.some((n) => stripLeadingSlash(n).toLowerCase() === wanted),
    );

    if (!match) throw new ServiceNotFoundError(name);
    return this.#docker.getContainer(match.Id);
  }

  async inspect(name: string): Promise<Docker.ContainerInspectInfo> {
    return (await this.resolve(name)).inspect();
  }

  /**
   * Reads recent log lines. Docker multiplexes stdout and stderr into a framed
   * stream when no TTY is attached, so the 8-byte headers are stripped here.
   */
  async logs(name: string, tailLines: number, sinceSeconds?: number): Promise<string> {
    const container = await this.resolve(name);
    const options: Record<string, unknown> = {
      stdout: true,
      stderr: true,
      tail: tailLines,
      timestamps: true,
    };
    if (sinceSeconds !== undefined) {
      options.since = Math.floor(Date.now() / 1000) - sinceSeconds;
    }

    const raw = (await container.logs(options as never)) as unknown as Buffer;
    return demultiplex(raw);
  }

  /** Single-shot resource sample. Percentages need two points, so Docker's own delta is used. */
  async stats(name: string): Promise<ServiceStats> {
    const container = await this.resolve(name);
    const sample = (await container.stats({ stream: false })) as Docker.ContainerStats;

    const cpuDelta =
      sample.cpu_stats.cpu_usage.total_usage - sample.precpu_stats.cpu_usage.total_usage;
    const systemDelta =
      (sample.cpu_stats.system_cpu_usage ?? 0) - (sample.precpu_stats.system_cpu_usage ?? 0);
    const cores = sample.cpu_stats.online_cpus ?? 1;

    const memoryUsed = sample.memory_stats.usage ?? null;
    const memoryLimit = sample.memory_stats.limit ?? null;

    return {
      name: stripLeadingSlash(name),
      cpuPercent:
        systemDelta > 0 && cpuDelta > 0
          ? round((cpuDelta / systemDelta) * cores * 100)
          : null,
      memoryUsedMb: memoryUsed === null ? null : round(memoryUsed / 1024 / 1024),
      memoryLimitMb: memoryLimit === null ? null : round(memoryLimit / 1024 / 1024),
      memoryPercent:
        memoryUsed !== null && memoryLimit ? round((memoryUsed / memoryLimit) * 100) : null,
    };
  }
}

function stripLeadingSlash(name: string): string {
  return name.startsWith("/") ? name.slice(1) : name;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * Strips Docker's stream framing. Each frame is an 8-byte header whose last
 * four bytes are a big-endian payload length. Falls back to the raw buffer if
 * the framing does not look well-formed.
 */
function demultiplex(buffer: Buffer): string {
  const chunks: string[] = [];
  let offset = 0;

  while (offset + 8 <= buffer.length) {
    const streamType = buffer[offset];
    if (streamType !== 0 && streamType !== 1 && streamType !== 2) {
      return buffer.toString("utf8");
    }
    const length = buffer.readUInt32BE(offset + 4);
    if (offset + 8 + length > buffer.length) break;
    chunks.push(buffer.toString("utf8", offset + 8, offset + 8 + length));
    offset += 8 + length;
  }

  return chunks.length > 0 ? chunks.join("") : buffer.toString("utf8");
}
