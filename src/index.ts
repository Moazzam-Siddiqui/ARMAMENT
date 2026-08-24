/**
 * Entry point. Validates configuration and reachability before listening, so a
 * broken setup surfaces at startup instead of midway through an incident.
 */

import { loadConfig } from "./config.js";
import { DockerClient } from "./docker.js";
import { createApp } from "./server.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const docker = new DockerClient(config);

  try {
    await docker.ping();
  } catch (error) {
    throw new Error(
      `Cannot reach the Docker daemon: ${error instanceof Error ? error.message : error}. ` +
        `Start Docker, or set DOCKER_HOST if it listens somewhere non-standard.`,
    );
  }

  const app = createApp(config, docker);

  app.listen(config.port, () => {
    console.log(`[sentinel-ops] listening on http://localhost:${config.port}/mcp`);
    console.log(
      `[sentinel-ops] managing containers labelled ${config.label.key}=${config.label.value}`,
    );
  });
}

main().catch((error: unknown) => {
  console.error(`[sentinel-ops] ${error instanceof Error ? error.message : error}`);
  process.exit(1);
});
