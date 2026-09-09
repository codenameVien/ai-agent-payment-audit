/**
 * Process entrypoints of the AEGIS local runtime.
 *
 * Three separate roles run as three separate processes on three separate ports, because
 * the trust boundaries are real: only the payment executor holds a signing key, only the
 * Evidence API holds the database, and the Provider Gateways hold neither. The local
 * runner generates an ephemeral test key per run and passes it to the executor alone.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";

import type { Hex } from "viem";

import { AegisEvidenceClient } from "./evidence-client.js";
import { MockFacilitator } from "./facilitator.js";
import { AegisPaymentExecutor } from "./payment-executor.js";
import { ProviderGateway } from "./provider-gateway.js";
import { mockProviderFor, MOCK_EXECUTION_MODE } from "./providers.js";
import { AegisAuthorizationSigner } from "./signer.js";

export const DEFAULT_NETWORK = "eip155:84532";

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function chainIdOf(network: string): number {
  const match = /^eip155:([0-9]+)$/.exec(network);
  if (match === null) throw new Error(`unsupported network: ${network}`);
  return Number(match[1]);
}

async function nodeRequest(request: IncomingMessage): Promise<Request> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (typeof value === "string") headers.set(name, value);
    else if (Array.isArray(value)) for (const item of value) headers.append(name, item);
  }
  const host = request.headers.host ?? "127.0.0.1";
  const method = request.method ?? "GET";
  return new Request(`http://${host}${request.url ?? "/"}`, {
    method,
    headers,
    ...(method === "GET" || method === "HEAD"
      ? {}
      : { body: Buffer.concat(chunks).toString("utf8") }),
  });
}

async function writeResponse(response: Response, target: ServerResponse): Promise<void> {
  target.statusCode = response.status;
  response.headers.forEach((value, name) => target.setHeader(name, value));
  target.end(Buffer.from(await response.arrayBuffer()));
}

function serve(handler: (request: Request) => Promise<Response>): Server {
  return createServer((incoming, outgoing) => {
    void (async () => {
      const response = await handler(await nodeRequest(incoming));
      await writeResponse(response, outgoing);
    })().catch((error: unknown) => {
      outgoing.statusCode = 500;
      outgoing.setHeader("content-type", "application/json");
      outgoing.end(
        JSON.stringify({ error: error instanceof Error ? error.message : "internal error" }),
      );
    });
  });
}

export function createProviderGateway(env: NodeJS.ProcessEnv = process.env): ProviderGateway {
  const providerId = required(env, "AEGIS_PROVIDER_ID");
  return new ProviderGateway({
    providerId,
    provider: mockProviderFor(providerId),
    evidence: new AegisEvidenceClient({
      baseUrl: required(env, "EVIDENCE_API_URL"),
      internalServiceToken: required(env, "INTERNAL_SERVICE_TOKEN"),
    }),
    facilitatorUrl: required(env, "AEGIS_FACILITATOR_URL"),
    network: env.AEGIS_NETWORK?.trim() || DEFAULT_NETWORK,
  });
}

export function createProviderGatewayServer(env: NodeJS.ProcessEnv = process.env): Server {
  const gateway = createProviderGateway(env);
  return serve((request) => gateway.handle(request));
}

export function createFacilitatorServer(): Server {
  const facilitator = new MockFacilitator();
  return serve((request) => facilitator.handle(request));
}

export function createPaymentExecutor(
  env: NodeJS.ProcessEnv = process.env,
): AegisPaymentExecutor {
  const network = env.AEGIS_NETWORK?.trim() || DEFAULT_NETWORK;
  const routes = JSON.parse(required(env, "AEGIS_GATEWAY_ROUTES_JSON")) as unknown;
  if (typeof routes !== "object" || routes === null || Array.isArray(routes)) {
    throw new Error("AEGIS_GATEWAY_ROUTES_JSON must be an object");
  }
  for (const [providerId, url] of Object.entries(routes)) {
    if (!providerId || typeof url !== "string" || !/^https?:\/\//.test(url)) {
      throw new Error("AEGIS_GATEWAY_ROUTES_JSON contains an invalid route");
    }
  }
  return new AegisPaymentExecutor({
    evidence: new AegisEvidenceClient({
      baseUrl: required(env, "EVIDENCE_API_URL"),
      internalServiceToken: required(env, "INTERNAL_SERVICE_TOKEN"),
    }),
    // The key never leaves this process, and the local runner supplies an ephemeral one.
    signer: new AegisAuthorizationSigner(
      required(env, "AEGIS_SIGNER_PRIVATE_KEY") as Hex,
      chainIdOf(network),
    ),
    gatewayRoutes: routes as Record<string, string>,
    network,
    executionMode: MOCK_EXECUTION_MODE,
  });
}

/**
 * The executor's own HTTP surface, kept compatible with the buyer API's `/execute` call.
 * `/address` exists so the runner can bind the ephemeral signer address as the buyer
 * wallet without ever transporting the key.
 */
export function createPaymentExecutorServer(env: NodeJS.ProcessEnv = process.env): Server {
  const executor = createPaymentExecutor(env);
  const serviceToken = required(env, "GATEWAY_SERVICE_TOKEN");
  return serve(async (request) => {
    const url = new URL(request.url);
    const json = (value: unknown, status = 200): Response =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "content-type": "application/json" },
      });
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", role: "payment-executor", executionMode: MOCK_EXECUTION_MODE });
    }
    if (request.headers.get("authorization") !== `Bearer ${serviceToken}`) {
      return json({ error: "unauthorized" }, 401);
    }
    if (request.method === "GET" && url.pathname === "/address") {
      return json({ buyerWalletAddress: executor.buyerAddress });
    }
    if (request.method === "POST" && url.pathname === "/execute") {
      const body = (await request.json()) as { purchaseId?: unknown; resourceBody?: unknown };
      if (typeof body.purchaseId !== "string" || body.purchaseId.length === 0) {
        return json({ error: "purchaseId is required" }, 400);
      }
      try {
        return json(
          await executor.execute({
            purchaseId: body.purchaseId,
            resourceBody: body.resourceBody ?? {},
          }),
        );
      } catch (error) {
        return json({ error: error instanceof Error ? error.message : "execution failed" }, 409);
      }
    }
    return json({ error: "not found" }, 404);
  });
}

const ROLES: Record<string, (env: NodeJS.ProcessEnv) => Server> = {
  "provider-gateway": createProviderGatewayServer,
  facilitator: () => createFacilitatorServer(),
  "payment-executor": createPaymentExecutorServer,
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const role = process.argv[2] ?? "";
  const factory = ROLES[role];
  if (factory === undefined) {
    throw new Error(`usage: aegis <${Object.keys(ROLES).join("|")}>`);
  }
  const port = Number(process.env.PORT ?? "0");
  if (!Number.isSafeInteger(port) || port <= 0) throw new Error("PORT must be positive");
  factory(process.env).listen(port, "127.0.0.1", () => {
    process.stdout.write(`aegis ${role} listening on 127.0.0.1:${port}\n`);
  });
}
