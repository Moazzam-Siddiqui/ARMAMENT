/**
 * HTTP surface for the MCP server.
 *
 * TrueForge connects to remote MCP servers over streamable HTTP with static
 * header auth, so this exposes a single POST endpoint behind a bearer check.
 * Each request gets a fresh server and transport: the harness holds the
 * conversation state, so there is nothing here worth keeping between calls,
 * and statelessness removes a class of cross-session bugs.
 */

import express, { type Express, type Request, type Response } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { timingSafeEqual } from "node:crypto";
import type { Config } from "./config.js";
import { DockerClient } from "./docker.js";
import { registerReadTools } from "./tools/read.js";
import { registerWriteTools } from "./tools/write.js";

const SERVER_INFO = { name: "sentinel-ops", version: "0.1.0" } as const;

function buildMcpServer(docker: DockerClient): McpServer {
  const server = new McpServer(SERVER_INFO);
  registerReadTools(server, docker);
  registerWriteTools(server, docker);
  return server;
}

/** Constant-time bearer comparison, so the token cannot be guessed byte by byte. */
function tokenMatches(provided: string, expected: string): boolean {
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function isAuthorised(req: Request, config: Config): boolean {
  const header = req.get("authorization");
  if (!header?.startsWith("Bearer ")) return false;
  return tokenMatches(header.slice("Bearer ".length).trim(), config.authToken);
}

export function createApp(config: Config, docker: DockerClient): Express {
  const app = express();
  app.use(express.json({ limit: "4mb" }));

  // Unauthenticated: used to check the process is alive without exposing anything.
  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok", server: SERVER_INFO.name });
  });

  app.post("/mcp", async (req: Request, res: Response) => {
    if (!isAuthorised(req, config)) {
      res.status(401).json({
        jsonrpc: "2.0",
        error: { code: -32001, message: "Unauthorized" },
        id: null,
      });
      return;
    }

    const server = buildMcpServer(docker);
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });

    // Tie both lifetimes to the response so a dropped connection cannot leak them.
    res.on("close", () => {
      void transport.close();
      void server.close();
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      console.error("[sentinel-ops] request failed:", error);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        });
      }
    }
  });

  // Streamable HTTP allows GET and DELETE for session handling; this server is
  // stateless, so it says so explicitly rather than returning a confusing 404.
  app.all("/mcp", (_req, res) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: { code: -32000, message: "Method not allowed; this server is stateless" },
      id: null,
    });
  });

  return app;
}
