import { randomBytes, randomUUID } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";

import type { Address, Hex } from "viem";

import { GeminiProviderAdapter } from "./adapters/gemini.js";
import { EvidenceSellerExecutionStore } from "./adapters/evidence-execution.js";
import { NemotronProviderAdapter } from "./adapters/nemotron.js";
import { SellerApplication } from "./application.js";
import type { ModelOffer, ProviderAdapter } from "./contracts.js";
import { LocalEip712QuoteSigner } from "./eip712.js";
import { SellerHttpTransport } from "./http.js";
import { SellerEngine, SystemClock } from "./seller-engine.js";
import { FacilitatorPaymentGate, Permit2ChallengeProvider } from "./x402.js";
import type { QuoteTermsReader, X402QuoteTerms } from "./x402.js";

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function positiveInt(env: NodeJS.ProcessEnv, name: string, fallback: number): number {
  const raw = env[name];
  const value = raw === undefined ? fallback : Number(raw);
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${name} must be positive`);
  return value;
}

function provider(env: NodeJS.ProcessEnv): ProviderAdapter {
  const providerId = required(env, "PROVIDER_ID");
  const apiKey = required(env, "PROVIDER_API_KEY");
  if (providerId === "gemini") return new GeminiProviderAdapter({ apiKey });
  if (providerId === "nemotron") return new NemotronProviderAdapter({ apiKey });
  throw new Error("PROVIDER_ID must be gemini or nemotron");
}

export class EvidenceQuoteTermsReader implements QuoteTermsReader {
  readonly #local: QuoteTermsReader;
  readonly #baseUrl: string;
  readonly #authorization: string;
  readonly #fetch: typeof fetch;

  constructor(local: QuoteTermsReader, env: NodeJS.ProcessEnv, fetchImpl: typeof fetch = fetch) {
    this.#local = local;
    this.#baseUrl = required(env, "EVIDENCE_API_URL").replace(/\/$/, "");
    this.#authorization = `Bearer ${required(env, "INTERNAL_SERVICE_TOKEN")}`;
    this.#fetch = fetchImpl;
  }

  async read(purchaseId: string, quoteId: string): Promise<X402QuoteTerms | null> {
    const local = await this.#local.read(purchaseId, quoteId);
    if (local !== null) return local;
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/seller-quotes/${encodeURIComponent(quoteId)}`,
      { headers: { authorization: this.#authorization } },
    );
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`evidence quote lookup failed with ${response.status}`);
    const body = await response.json() as {
      model_id: string;
      amount_units: number;
      token: Address;
      pay_to: Address;
      expires_at: string;
    };
    return {
      modelId: body.model_id,
      amount: BigInt(body.amount_units),
      token: body.token,
      payTo: body.pay_to,
      expiresAt: BigInt(Math.floor(Date.parse(body.expires_at) / 1000)),
    };
  }
}

async function nodeRequest(request: IncomingMessage): Promise<Request> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  const host = request.headers.host ?? "localhost";
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (value !== undefined) headers.set(name, Array.isArray(value) ? value.join(",") : value);
  }
  const body = chunks.length === 0 ? undefined : Buffer.concat(chunks);
  return new Request(`http://${host}${request.url ?? "/"}`, {
    method: request.method,
    headers,
    ...(body === undefined ? {} : { body }),
  });
}

async function writeResponse(response: Response, target: ServerResponse): Promise<void> {
  target.statusCode = response.status;
  response.headers.forEach((value, name) => target.setHeader(name, value));
  target.end(Buffer.from(await response.arrayBuffer()));
}

export function createSellerTransport(env: NodeJS.ProcessEnv = process.env): SellerHttpTransport {
  const signer = new LocalEip712QuoteSigner(
    required(env, "SELLER_PRIVATE_KEY") as Hex,
    {
      chainId: 84532,
      verifyingContract: required(env, "QUOTE_VERIFYING_CONTRACT") as Address,
    },
  );
  const offer: ModelOffer = {
    modelId: required(env, "MODEL_ID"),
    modelVersion: env.MODEL_VERSION ?? required(env, "MODEL_ID"),
    amount: BigInt(required(env, "MODEL_PRICE_UNITS")),
    expectedLatencyMs: positiveInt(env, "MODEL_EXPECTED_LATENCY_MS", 5000),
    inputLimit: positiveInt(env, "MODEL_INPUT_LIMIT", 32768),
    outputLimit: positiveInt(env, "MODEL_OUTPUT_LIMIT", 4096),
    enabled: true,
  };
  const engine = new SellerEngine({
    config: {
      sellerAgentId: required(env, "SELLER_AGENT_ID"),
      erc8004AgentId: BigInt(required(env, "ERC8004_AGENT_ID")),
      providerId: required(env, "PROVIDER_ID"),
      agentWallet: signer.address,
      token: required(env, "TOKEN_ADDRESS") as Address,
      payTo: required(env, "PAY_TO_ADDRESS") as Address,
      quoteLifetimeSeconds: positiveInt(env, "QUOTE_LIFETIME_SECONDS", 300),
      models: [offer],
    },
    provider: provider(env),
    signer,
    clock: new SystemClock(),
    quoteIds: { next: () => `quote-${randomUUID()}` },
    nonces: { next: () => BigInt(`0x${randomBytes(32).toString("hex")}`) },
  });
  const facilitatorHeaders = env.FACILITATOR_AUTHORIZATION
    ? { authorization: env.FACILITATOR_AUTHORIZATION }
    : undefined;
  const quoteTerms = new EvidenceQuoteTermsReader(engine, env);
  const gate = new FacilitatorPaymentGate({
    quotes: quoteTerms,
    facilitatorUrl: required(env, "FACILITATOR_URL"),
    ...(facilitatorHeaders === undefined ? {} : { facilitatorHeaders }),
  });
  const application = new SellerApplication(
    engine,
    gate,
    new EvidenceSellerExecutionStore({
      baseUrl: required(env, "EVIDENCE_API_URL"),
      internalServiceToken: required(env, "INTERNAL_SERVICE_TOKEN"),
    }),
  );
  return new SellerHttpTransport(
    application,
    required(env, "PROVIDER_ID"),
    new Permit2ChallengeProvider(quoteTerms, required(env, "PROVIDER_ID")),
    required(env, "INTERNAL_SERVICE_TOKEN"),
  );
}

export function createSellerServer(env: NodeJS.ProcessEnv = process.env) {
  const transport = createSellerTransport(env);
  return createServer((request, response) => {
    void nodeRequest(request)
      .then((incoming) => transport.handle(incoming))
      .then((outgoing) => writeResponse(outgoing, response))
      .catch((error: unknown) => {
        response.statusCode = 500;
        response.setHeader("content-type", "application/json");
        response.end(JSON.stringify({ error: error instanceof Error ? error.message : "error" }));
      });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = positiveInt(process.env, "PORT", 8080);
  createSellerServer().listen(port, "0.0.0.0", () => {
    process.stdout.write(`seller service listening on ${port}\n`);
  });
}
