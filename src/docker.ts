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
  readonly #labelKey: string;
  readonly #labelValue: string;

  constructor(config: Config) {
    this.#docker = new Docker();
    this.#label = `${config.label.key}=${config.label.value}`;
    this.#labelKey = config.label.key;
    this.#labelValue = config.label.value;
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

  /** Restarts a service in place. Config and image are unchanged. */
  async restart(name: string, timeoutSeconds = 10): Promise<void> {
    const container = await this.resolve(name);
    await container.restart({ t: timeoutSeconds });
  }

  /**
   * Raises the memory ceiling of a running service. Swap is pinned to the same
   * value: left alone, Docker grants swap equal to twice the limit, which turns
   * an out-of-memory crash into silent thrashing that is harder to diagnose.
   */
  async setMemoryLimit(name: string, megabytes: number): Promise<{ previousMb: number | null }> {
    const container = await this.resolve(name);
    const before = await container.inspect();
    const previous = before.HostConfig.Memory;
    const bytes = Math.floor(megabytes * 1024 * 1024);

    await container.update({ Memory: bytes, MemorySwap: bytes });

    return { previousMb: previous ? round(previous / 1024 / 1024) : null };
  }

  /**
   * Replaces a service with the same configuration on a different image tag.
   *
   * Docker cannot swap the image of an existing container, so this destroys and
   * rebuilds it. That makes it the one genuinely unrecoverable operation here,
   * and it is ordered accordingly: the replacement image is verified present
   * before anything is torn down, and a failed rebuild is retried once on the
   * original image so a bad tag cannot leave the service simply gone.
   */
  async recreateWithImage(
    name: string,
    image: string,
  ): Promise<{ previousImage: string; recovered: boolean }> {
    const container = await this.resolve(name);
    const info = await container.inspect();
    const previousImage = info.Config.Image;

    if (previousImage === image) {
      throw new Error(`${name} already runs ${image}; nothing to roll back to.`);
    }

    // Verified before teardown: pulling here could stall for minutes mid-incident.
    const available = await this.#docker.listImages({ filters: { reference: [image] } });
    if (available.length === 0) {
      throw new Error(
        `Image "${image}" is not present locally. Pull it first; this tool will ` +
          `not fetch images while a service is being rebuilt.`,
      );
    }

    const spec = buildRecreateSpec(info, image);

    // The label must survive, or the rebuilt service falls outside managed scope
    // and no tool here can reach it again.
    if (spec.Labels?.[this.#labelKey] !== this.#labelValue) {
      throw new Error(
        `Refusing to rebuild ${name}: its managed label is missing, so the ` +
          `replacement would be unreachable by this agent.`,
      );
    }

    await container.stop({ t: 10 }).catch(() => undefined);
    await container.remove({ force: true });

    try {
      const created = await this.#docker.createContainer(spec);
      await created.start();
      return { previousImage, recovered: false };
    } catch (error) {
      const rebuilt = await this.#docker
        .createContainer(buildRecreateSpec(info, previousImage))
        .then(async (c) => {
          await c.start();
          return true;
        })
        .catch(() => false);

      throw new Error(
        `Rebuilding ${name} on ${image} failed: ${error instanceof Error ? error.message : error}. ` +
          (rebuilt
            ? `The service was restored on ${previousImage}.`
            : `THE SERVICE IS DOWN and could not be restored on ${previousImage}. ` +
              `This needs manual recovery now.`),
      );
    }
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

/**
 * Rebuilds a creation spec from an inspected container, swapping only the image.
 * HostConfig carries ports, mounts, restart policy and resource limits, so
 * copying it wholesale is what keeps the replacement faithful to the original.
 */
function buildRecreateSpec(
  info: Docker.ContainerInspectInfo,
  image: string,
): Docker.ContainerCreateOptions {
  return {
    name: stripLeadingSlash(info.Name),
    Image: image,
    Cmd: info.Config.Cmd,
    Entrypoint: info.Config.Entrypoint,
    Env: info.Config.Env,
    Labels: info.Config.Labels,
    ExposedPorts: info.Config.ExposedPorts,
    WorkingDir: info.Config.WorkingDir,
    User: info.Config.User,
    HostConfig: info.HostConfig,
  };
}
