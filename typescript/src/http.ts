import { randomUUID } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { AuthInfo } from "@modelcontextprotocol/sdk/server/auth/types.js";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { zodToJsonSchema } from "zod-to-json-schema";

import { INSTRUCTIONS, toolAnnotations, type AdsToolDefinition } from "./core.js";
import {
  configureHostedEnvironment,
  emitMcpRequestTelemetry,
  hostedBodyMaxBytes,
  isHttpBaseUrlOverrideAllowed,
  isHttpWriteModeAllowed,
  isHostedDisabled,
  isHostedPublicMode,
  makeRequestContext,
  rateLimitHeaders,
  rateLimitMcpRequest,
  summariseMcpRequest,
  type HostedRequestContext,
  type McpRequestSummary,
} from "./hosted.js";

type AuthedIncomingMessage = IncomingMessage & { auth?: AuthInfo };
type CreateMcpServer = () => McpServer;

export interface HttpServerHandle {
  close: () => Promise<void>;
  url: string;
}

export interface HttpServerOptions {
  host?: string;
  port?: number;
  mcpPath?: string;
  healthPath?: string;
}

interface McpSession {
  id?: string;
  mcpServer: McpServer;
  transport: StreamableHTTPServerTransport;
  closing: boolean;
  lastSeenAt: number;
}

export async function startHttpServer(options: HttpServerOptions = {}): Promise<HttpServerHandle> {
  const { createOpenAIAdsMcpServer, registeredToolDefinitions } = await import("./index.js");
  configureHostedEnvironment();

  const host = options.host ?? process.env.HOST ?? (process.env.PORT ? "0.0.0.0" : "127.0.0.1");
  const port = options.port ?? parsePort(process.env.PORT ?? process.env.OPENAI_ADS_MCP_HTTP_PORT);
  const mcpPath = normalisePath(options.mcpPath ?? process.env.OPENAI_ADS_MCP_HTTP_PATH ?? "/mcp");
  const healthPath = normalisePath(options.healthPath ?? process.env.OPENAI_ADS_MCP_HEALTH_PATH ?? "/healthz");
  const serverCardPath = "/.well-known/mcp/server-card.json";
  const sessions = new Map<string, McpSession>();

  const httpServer = createServer(async (req: AuthedIncomingMessage, res) => {
    const started = Date.now();
    let requestSummary: McpRequestSummary | undefined;
    let hostedContext: HostedRequestContext | undefined;
    try {
      const path = requestPath(req);
      setCorsHeaders(res);

      if (req.method === "OPTIONS") {
        res.writeHead(204);
        res.end();
        return;
      }

      if (req.method === "GET" && [healthPath, "/health", "/ready"].includes(path)) {
        writeJson(res, 200, {
          ok: !isHostedDisabled(),
          disabled: isHostedDisabled(),
          name: "openai-ads-mcp",
          transport: "streamable-http",
          readonly: process.env.OPENAI_ADS_MCP_READONLY === "1",
          hosted_public: isHostedPublicMode(),
        });
        return;
      }

      if (req.method === "GET" && path === serverCardPath) {
        if (!edgeSecretOk(req)) {
          writeJson(res, 403, { error: "forbidden" });
          return;
        }
        writeJson(res, 200, serverCard());
        return;
      }

      if (!edgeSecretOk(req)) {
        writeJson(res, 403, { error: "forbidden" });
        return;
      }

      if (path !== mcpPath) {
        writeJson(res, 404, { error: "not_found" });
        return;
      }

      if (!originOk(req)) {
        writeJson(res, 403, { error: "forbidden_origin" });
        return;
      }

      if (isHostedDisabled()) {
        writeJson(res, 503, {
          error: "hosted_endpoint_disabled",
          message: "The hosted OpenAI Ads MCP endpoint is temporarily disabled. Local npm and PyPI installs still work.",
        });
        return;
      }

      if (!["GET", "POST", "DELETE"].includes(req.method ?? "")) {
        writeJson(res, 405, { error: "method_not_allowed" });
        return;
      }

      const contentLength = Number.parseInt(singleHeader(req, "content-length"), 10);
      if (Number.isFinite(contentLength) && contentLength > hostedBodyMaxBytes()) {
        writeJson(res, 413, { error: "request_too_large", max_bytes: hostedBodyMaxBytes() });
        return;
      }

      const auth = requestAuth(req);
      if (!auth.ok) {
        writeJson(res, auth.status, { error: auth.code });
        return;
      }
      let parsedBody: unknown;
      if (req.method === "POST") {
        const bodyResult = await readJsonBody(req);
        if (!bodyResult.ok) {
          writeJson(res, bodyResult.status, { error: bodyResult.code, ...(bodyResult.maxBytes ? { max_bytes: bodyResult.maxBytes } : {}) });
          return;
        }
        parsedBody = bodyResult.body;
        requestSummary = summariseMcpRequest(parsedBody);
      } else {
        requestSummary = { methods: [req.method?.toLowerCase() ?? "unknown"], toolNames: [], toolArgs: [] };
      }

      hostedContext = makeRequestContext({
        method: requestSummary.methods.join(","),
        requestBytes: requestByteCount(req, parsedBody),
        remoteAddress: clientIp(req),
        userAgent: singleHeader(req, "user-agent"),
        openaiAdsApiKey: auth.openaiAdsApiKey,
        clientInfo: requestSummary.clientInfo,
      });

      const rateLimit = rateLimitMcpRequest(requestSummary, hostedContext);
      if (!rateLimit.allowed) {
        writeJson(res, 429, { error: "rate_limited", message: "Too many hosted OpenAI Ads MCP requests. Please retry later." }, rateLimitHeaders(rateLimit));
        emitMcpRequestTelemetry({
          context: hostedContext,
          summary: requestSummary,
          status: "rate_limited",
          httpStatus: 429,
          durationMs: Date.now() - started,
        });
        return;
      }

      if (isHostedPublicMode() && parsedBody !== undefined) {
        const discoveryResponse = hostedDiscoveryResponse(parsedBody, registeredToolDefinitions());
        if (discoveryResponse && !containsInitializeRequest(parsedBody)) {
          writeJson(res, 200, discoveryResponse);
          emitMcpRequestTelemetry({
            context: hostedContext,
            summary: requestSummary,
            status: "ok",
            httpStatus: 200,
            durationMs: Date.now() - started,
          });
          return;
        }
      }

      if (isHostedPublicMode() && requestSummary.methods.includes("tools/call") && !auth.openaiAdsApiKey && requiresAdsApiKey(requestSummary)) {
        writeJson(res, 200, missingApiKeyToolResult(parsedBody));
        emitMcpRequestTelemetry({
          context: hostedContext,
          summary: requestSummary,
          status: "missing_api_key",
          httpStatus: 200,
          durationMs: Date.now() - started,
        });
        return;
      }

      req.auth = withHostedContext(auth.info, hostedContext);
      const session = await resolveMcpSession({
        req,
        res,
        parsedBody,
        sessions,
        createOpenAIAdsMcpServer,
      });
      if (!session) {
        return;
      }
      await session.transport.handleRequest(req, res, parsedBody);
      emitMcpRequestTelemetry({
        context: hostedContext,
        summary: requestSummary,
        status: res.statusCode >= 400 ? "error" : "ok",
        httpStatus: res.statusCode,
        durationMs: Date.now() - started,
      });
    } catch (error) {
      if (!res.headersSent) {
        writeJson(res, 500, { error: "internal_error" });
      } else {
        res.end();
      }
      if (requestSummary && hostedContext) {
        emitMcpRequestTelemetry({
          context: hostedContext,
          summary: requestSummary,
          status: "error",
          httpStatus: res.statusCode || 500,
          durationMs: Date.now() - started,
        });
      }
      process.stderr.write(`openai-ads-mcp HTTP transport error: ${errorName(error)}\n`);
    }
  });

  await new Promise<void>((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(port, host, () => {
      httpServer.off("error", reject);
      resolve();
    });
  });

  const address = httpServer.address() as AddressInfo;
  const displayHost = host === "0.0.0.0" ? "127.0.0.1" : host;
  const url = `http://${displayHost}:${address.port}${mcpPath}`;
  process.stderr.write(`openai-ads-mcp listening on ${url}\n`);

  return {
    url,
    close: async () => {
      await Promise.all([...sessions.values()].map((session) => closeMcpSession(session, sessions)));
      await new Promise<void>((resolve, reject) => {
        httpServer.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}

async function resolveMcpSession(input: {
  req: IncomingMessage;
  res: ServerResponse;
  parsedBody: unknown;
  sessions: Map<string, McpSession>;
  createOpenAIAdsMcpServer: CreateMcpServer;
}): Promise<McpSession | null> {
  await sweepExpiredSessions(input.sessions);
  const sessionId = singleHeader(input.req, "mcp-session-id");
  if (sessionId) {
    const session = input.sessions.get(sessionId);
    if (!session) {
      writeJson(input.res, 404, jsonRpcError(-32001, "Session not found"));
      return null;
    }
    session.lastSeenAt = Date.now();
    return session;
  }

  if (input.req.method === "POST" && containsInitializeRequest(input.parsedBody)) {
    return createMcpSession(input.createOpenAIAdsMcpServer, input.sessions);
  }

  writeJson(input.res, 400, jsonRpcError(-32000, "Bad Request: Mcp-Session-Id header is required"));
  return null;
}

async function createMcpSession(createOpenAIAdsMcpServer: CreateMcpServer, sessions: Map<string, McpSession>): Promise<McpSession> {
  let session: McpSession;
  const mcpServer = createOpenAIAdsMcpServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
    onsessioninitialized: (sessionId) => {
      session.id = sessionId;
      session.lastSeenAt = Date.now();
      sessions.set(sessionId, session);
    },
    onsessionclosed: (sessionId) => {
      const current = sessions.get(sessionId);
      if (current) {
        void closeMcpSession(current, sessions);
      }
    },
  });
  session = {
    mcpServer,
    transport,
    closing: false,
    lastSeenAt: Date.now(),
  };
  transport.onclose = () => {
    if (session.id) {
      sessions.delete(session.id);
    }
  };
  await mcpServer.connect(transport);
  return session;
}

async function closeMcpSession(session: McpSession, sessions: Map<string, McpSession>): Promise<void> {
  if (session.closing) {
    return;
  }
  session.closing = true;
  if (session.id) {
    sessions.delete(session.id);
  }
  await Promise.allSettled([
    session.transport.close(),
    session.mcpServer.close(),
  ]);
}

function containsInitializeRequest(body: unknown): boolean {
  const messages = Array.isArray(body) ? body : [body];
  return messages.some((message) => {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      return false;
    }
    return (message as Record<string, unknown>).method === "initialize";
  });
}

function jsonRpcError(code: number, message: string): Record<string, unknown> {
  return {
    jsonrpc: "2.0",
    error: { code, message },
    id: null,
  };
}

async function sweepExpiredSessions(sessions: Map<string, McpSession>): Promise<void> {
  const ttlMs = parsePositiveInt(process.env.OPENAI_ADS_MCP_HTTP_SESSION_TTL_MS, 30 * 60 * 1000);
  const cutoff = Date.now() - ttlMs;
  const expired = [...sessions.values()].filter((session) => session.lastSeenAt < cutoff);
  await Promise.all(expired.map((session) => closeMcpSession(session, sessions)));
}

function requestAuth(req: IncomingMessage):
  | { ok: true; info: AuthInfo; openaiAdsApiKey?: string }
  | { ok: false; status: number; code: string } {
  const requiredToken = (process.env.OPENAI_ADS_MCP_HTTP_TOKEN ?? "").trim();
  if (requiredToken && bearerToken(req.headers.authorization) !== requiredToken) {
    return { ok: false, status: 401, code: "unauthorized" };
  }

  const openaiAdsApiKey = singleHeader(req, "x-openai-ads-api-key");
  const openaiAdsApiBaseUrl = singleHeader(req, "x-openai-ads-api-base-url");
  if (openaiAdsApiBaseUrl && !isHttpBaseUrlOverrideAllowed()) {
    return { ok: false, status: 400, code: "openai_ads_api_base_url_not_allowed" };
  }
  if ((openaiAdsApiKey && openaiAdsApiKey.length > 400) || (openaiAdsApiBaseUrl && openaiAdsApiBaseUrl.length > 300)) {
    return { ok: false, status: 400, code: "invalid_openai_ads_credentials" };
  }

  return {
    ok: true,
    info: {
      token: requiredToken ? "mcp-http-token" : "anonymous",
      clientId: "openai-ads-mcp-http",
      scopes: isHttpWriteModeAllowed() ? ["ads:read", "ads:write"] : ["ads:read"],
      extra: {
        ...(openaiAdsApiKey ? { openaiAdsApiKey } : {}),
        ...(openaiAdsApiBaseUrl && isHttpBaseUrlOverrideAllowed() ? { openaiAdsApiBaseUrl } : {}),
      },
    },
    ...(openaiAdsApiKey ? { openaiAdsApiKey } : {}),
  };
}

function withHostedContext(info: AuthInfo, context: HostedRequestContext): AuthInfo {
  return {
    ...info,
    extra: {
      ...(info.extra ?? {}),
      openaiAdsMcpContext: context,
    },
  };
}

function bearerToken(value: string | undefined): string {
  const [scheme, token] = (value ?? "").split(/\s+/, 2);
  if (scheme?.toLowerCase() !== "bearer" || !token) {
    return "";
  }
  return token.trim();
}

function singleHeader(req: IncomingMessage, name: string): string {
  const value = req.headers[name];
  if (Array.isArray(value)) {
    return (value[0] ?? "").trim();
  }
  return (value ?? "").trim();
}

function edgeSecretOk(req: IncomingMessage): boolean {
  const expected = (process.env.OPENAI_ADS_MCP_EDGE_SECRET ?? "").trim();
  if (!expected) {
    return true;
  }
  return singleHeader(req, "x-trakkr-edge-secret") === expected;
}

function requestPath(req: IncomingMessage): string {
  return new URL(req.url ?? "/", "http://localhost").pathname;
}

function normalisePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed || trimmed === "/") {
    return "/";
  }
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}`;
}

function parsePort(value: string | undefined): number {
  const parsed = Number.parseInt(value ?? "", 10);
  if (Number.isInteger(parsed) && parsed >= 0 && parsed <= 65_535) {
    return parsed;
  }
  return 8080;
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function setCorsHeaders(res: ServerResponse): void {
  const origin = process.env.OPENAI_ADS_MCP_HTTP_CORS_ORIGIN?.trim() || "*";
  const allowedHeaders = [
    "Authorization",
    "Content-Type",
    "Mcp-Protocol-Version",
    "Mcp-Session-Id",
    "X-OpenAI-Ads-API-Key",
    ...(isHttpBaseUrlOverrideAllowed() ? ["X-OpenAI-Ads-API-Base-Url"] : []),
  ];
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", allowedHeaders.join(","));
  res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
}

function originOk(req: IncomingMessage): boolean {
  const origin = singleHeader(req, "origin");
  if (!origin) {
    return true;
  }
  const allowedOrigins = configuredAllowedOrigins();
  if (allowedOrigins.has("*") || allowedOrigins.has(origin)) {
    return true;
  }
  try {
    const parsed = new URL(origin);
    const host = singleHeader(req, "host").split(":")[0]?.toLowerCase();
    if (host && parsed.hostname.toLowerCase() === host) {
      return true;
    }
    return isLoopbackHost(parsed.hostname);
  } catch {
    return false;
  }
}

function configuredAllowedOrigins(): Set<string> {
  const values = new Set<string>();
  for (const raw of (process.env.OPENAI_ADS_MCP_HTTP_ALLOWED_ORIGINS ?? "").split(",")) {
    const value = raw.trim();
    if (value) {
      values.add(value);
    }
  }
  const corsOrigin = process.env.OPENAI_ADS_MCP_HTTP_CORS_ORIGIN?.trim();
  if (corsOrigin && corsOrigin !== "*") {
    values.add(corsOrigin);
  }
  return values;
}

function isLoopbackHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1" || normalized.endsWith(".localhost");
}

function writeJson(res: ServerResponse, status: number, value: unknown, headers: Record<string, string> = {}): void {
  res.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    ...headers,
  });
  res.end(JSON.stringify(value));
}

async function readJsonBody(req: IncomingMessage): Promise<
  | { ok: true; body: unknown }
  | { ok: false; status: number; code: string; maxBytes?: number }
> {
  const maxBytes = hostedBodyMaxBytes();
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.byteLength;
    if (total > maxBytes) {
      return { ok: false, status: 413, code: "request_too_large", maxBytes };
    }
    chunks.push(buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text.trim()) {
    return { ok: false, status: 400, code: "invalid_json" };
  }
  try {
    return { ok: true, body: JSON.parse(text) };
  } catch {
    return { ok: false, status: 400, code: "invalid_json" };
  }
}

function requestByteCount(req: IncomingMessage, parsedBody: unknown): number | undefined {
  const contentLength = Number.parseInt(singleHeader(req, "content-length"), 10);
  if (Number.isFinite(contentLength)) {
    return contentLength;
  }
  if (parsedBody !== undefined) {
    return Buffer.byteLength(JSON.stringify(parsedBody), "utf8");
  }
  return undefined;
}

function clientIp(req: IncomingMessage): string {
  const forwarded = singleHeader(req, "cf-connecting-ip") ||
    singleHeader(req, "x-forwarded-for").split(",")[0]?.trim();
  return forwarded || req.socket.remoteAddress || "unknown";
}

function serverCard(): Record<string, unknown> {
  return {
    name: "io.github.trakkr-aisearch/openai-ads-mcp",
    title: "OpenAI Ads MCP",
    description: "Read-only hosted OpenAI Ads MCP endpoint for ChatGPT Ads discovery and insights. Users provide their own OpenAI Ads API key per request.",
    version: "0.1.7",
    protocol: "mcp",
    transport: {
      type: "streamable-http",
      url: "https://openai-ads-mcp.trakkr.ai/mcp",
    },
    authentication: {
      type: "header",
      header: "X-OpenAI-Ads-API-Key",
      required_for: "tool_calls",
    },
    capabilities: {
      readonly: true,
      writes: false,
    },
    publisher: {
      name: "Trakkr",
      url: "https://trakkr.ai",
    },
    repository: "https://github.com/trakkr-aisearch/openai-ads-mcp",
  };
}

function requiresAdsApiKey(summary: McpRequestSummary): boolean {
  return summary.toolNames.some((name) => name !== "draft_context_hints");
}

function missingApiKeyToolResult(body: unknown): Record<string, unknown> | Record<string, unknown>[] {
  const messages = Array.isArray(body) ? body : [body];
  const responses = messages
    .filter((message): message is Record<string, unknown> => !!message && typeof message === "object" && !Array.isArray(message))
    .map((message) => ({
      jsonrpc: "2.0",
      id: message.id ?? null,
      result: {
        content: [{
          type: "text",
          text: JSON.stringify({
            error: true,
            message: "This hosted OpenAI Ads MCP endpoint requires X-OpenAI-Ads-API-Key for Ads API tool calls. Discovery and tools/list work without a key.",
          }),
        }],
        isError: true,
      },
    }));
  return Array.isArray(body) ? responses : responses[0] ?? {
    jsonrpc: "2.0",
    id: null,
    result: {
      content: [{
        type: "text",
        text: JSON.stringify({ error: true, message: "Missing X-OpenAI-Ads-API-Key." }),
      }],
      isError: true,
    },
  };
}

function hostedDiscoveryResponse(body: unknown, tools: AdsToolDefinition[]): Record<string, unknown> | Record<string, unknown>[] | null {
  const messages = Array.isArray(body) ? body : [body];
  const responses: Record<string, unknown>[] = [];
  for (const message of messages) {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      continue;
    }
    const record = message as Record<string, unknown>;
    if (record.method === "initialize") {
      responses.push({
        jsonrpc: "2.0",
        id: record.id ?? null,
        result: {
          protocolVersion: "2025-06-18",
          capabilities: {
            resources: { listChanged: true },
            tools: { listChanged: true },
          },
          serverInfo: { name: "OpenAI Ads", version: "0.1.7" },
          instructions: INSTRUCTIONS,
        },
      });
    } else if (record.method === "tools/list") {
      responses.push({
        jsonrpc: "2.0",
        id: record.id ?? null,
        result: {
          tools: tools.map((tool) => ({
            name: tool.name,
            description: tool.description,
            inputSchema: zodToJsonSchema(z.object(tool.inputSchema)),
            annotations: toolAnnotations(tool),
          })),
        },
      });
    } else if (record.method === "resources/list") {
      responses.push({
        jsonrpc: "2.0",
        id: record.id ?? null,
        result: {
          resources: [{
            uri: "trakkr://ai-visibility-funnel",
            name: "Trakkr AI visibility funnel",
            description: "How paid OpenAI Ads data complements organic AI visibility tracking in Trakkr.",
            mimeType: "text/plain",
          }],
        },
      });
    }
  }
  if (!responses.length) {
    return null;
  }
  return Array.isArray(body) ? responses : responses[0];
}

function errorName(error: unknown): string {
  return error instanceof Error ? error.name : "Error";
}
