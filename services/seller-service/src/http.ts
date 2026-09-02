import type {
  PaymentSettlement,
  ProviderResult,
  QuoteRequest,
  SignedSellerQuote,
} from "./contracts.js";

interface SellerApplicationPort {
  quote(request: QuoteRequest): Promise<SignedSellerQuote>;
  paidInference(args: {
    purchaseId: string;
    quoteId: string;
    prompt: string;
    paymentSignature?: string;
  }): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }>;
  recover?(purchaseId: string, quoteId: string): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }>;
}

export interface PaymentChallengeProvider {
  challenge(purchaseId: string, quoteId: string, resourceUrl: string): Promise<unknown>;
}

function json(
  value: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(
    JSON.stringify(value, (_key, item: unknown) =>
      typeof item === "bigint" ? item.toString() : item,
    ),
    { status, headers: { "content-type": "application/json", ...extraHeaders } },
  );
}

function asQuoteRequest(value: unknown): QuoteRequest {
  if (typeof value !== "object" || value === null) throw new Error("invalid quote request");
  const input = value as Record<string, unknown>;
  const allowed = new Set([
    "purchaseId", "requestId", "minInputLimit", "minOutputLimit",
    "maxLatencyMs", "preferredModelId",
  ]);
  if (Object.keys(input).some((name) => !allowed.has(name))) {
    throw new Error("unknown quote request field");
  }
  for (const name of ["purchaseId", "requestId"] as const) {
    if (typeof input[name] !== "string" || input[name].trim().length === 0) {
      throw new Error(`invalid ${name}`);
    }
  }
  for (const name of ["minInputLimit", "minOutputLimit"] as const) {
    if (!Number.isSafeInteger(input[name]) || Number(input[name]) <= 0) {
      throw new Error(`invalid ${name}`);
    }
  }
  if (
    input.maxLatencyMs !== undefined &&
    (!Number.isSafeInteger(input.maxLatencyMs) || Number(input.maxLatencyMs) <= 0)
  ) {
    throw new Error("invalid maxLatencyMs");
  }
  if (
    input.preferredModelId !== undefined &&
    (
      typeof input.preferredModelId !== "string" ||
      input.preferredModelId.trim().length === 0
    )
  ) {
    throw new Error("invalid preferredModelId");
  }
  return {
    purchaseId: (input.purchaseId as string).trim(),
    requestId: (input.requestId as string).trim(),
    minInputLimit: input.minInputLimit as number,
    minOutputLimit: input.minOutputLimit as number,
    ...(input.maxLatencyMs === undefined ? {} : { maxLatencyMs: input.maxLatencyMs as number }),
    ...(input.preferredModelId === undefined
      ? {}
      : { preferredModelId: (input.preferredModelId as string).trim() }),
  };
}

export class SellerHttpTransport {
  readonly #application: SellerApplicationPort;
  readonly #providerId: string;
  readonly #paymentChallenges?: PaymentChallengeProvider;
  readonly #internalServiceToken?: string;

  constructor(
    application: SellerApplicationPort,
    providerId: string,
    paymentChallenges?: PaymentChallengeProvider,
    internalServiceToken?: string,
  ) {
    this.#application = application;
    this.#providerId = providerId;
    this.#paymentChallenges = paymentChallenges;
    this.#internalServiceToken = internalServiceToken;
  }

  async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", providerId: this.#providerId });
    }
    if (request.method === "POST" && url.pathname === "/internal/quotes") {
      if (
        this.#internalServiceToken !== undefined &&
        request.headers.get("authorization") !== `Bearer ${this.#internalServiceToken}`
      ) return json({ error: "unauthorized" }, 401);
      try {
        const quoteRequest = asQuoteRequest(await request.json());
        return json(await this.#application.quote(quoteRequest));
      } catch (error) {
        return json({ error: error instanceof Error ? error.message : "invalid request" }, 400);
      }
    }
    if (request.method === "POST" && url.pathname === "/internal/executions/recover") {
      if (
        this.#internalServiceToken === undefined ||
        request.headers.get("authorization") !== `Bearer ${this.#internalServiceToken}`
      ) return json({ error: "unauthorized" }, 401);
      if (this.#application.recover === undefined) {
        return json({ error: "seller recovery unavailable" }, 503);
      }
      try {
        const body = await request.json() as {
          purchaseId?: unknown;
          quoteId?: unknown;
        };
        if (typeof body.purchaseId !== "string" || typeof body.quoteId !== "string") {
          return json({ error: "invalid recovery request" }, 400);
        }
        const paid = await this.#application.recover(body.purchaseId, body.quoteId);
        return json(
          paid.settlement.success && paid.result !== undefined
            ? paid.result
            : {
                error:
                  paid.providerError ??
                  paid.settlement.errorReason ??
                  "payment settlement failed",
              },
          paid.settlement.success
            ? paid.result === undefined ? 502 : 200
            : 402,
          {
            "PAYMENT-RESPONSE": Buffer.from(
              JSON.stringify(paid.settlement),
              "utf8",
            ).toString("base64"),
          },
        );
      } catch (error) {
        return json({ error: error instanceof Error ? error.message : "recovery failed" }, 409);
      }
    }
    if (request.method === "POST" && url.pathname === "/v1/inference") {
      const purchaseId = url.searchParams.get("purchaseId") ?? "";
      const quoteId = url.searchParams.get("quoteId") ?? "";
      try {
        const body = (await request.json()) as { prompt?: unknown };
        if (!purchaseId || !quoteId || typeof body.prompt !== "string") {
          return json({ error: "invalid inference request" }, 400);
        }
        const paid = await this.#application.paidInference({
            purchaseId,
            quoteId,
            prompt: body.prompt,
            paymentSignature: request.headers.get("PAYMENT-SIGNATURE") ?? undefined,
          });
        return json(
          paid.settlement.success && paid.result !== undefined
            ? paid.result
            : {
                error:
                  paid.providerError ??
                  paid.settlement.errorReason ??
                  "payment settlement failed",
              },
          paid.settlement.success
            ? paid.result === undefined ? 502 : 200
            : 402,
          {
            "PAYMENT-RESPONSE": Buffer.from(
              JSON.stringify(paid.settlement),
              "utf8",
            ).toString("base64"),
          },
        );
      } catch (error) {
        if (error instanceof Error && error.message === "payment required") {
          const challenge = this.#paymentChallenges === undefined
            ? undefined
            : await this.#paymentChallenges.challenge(purchaseId, quoteId, request.url);
          return json(
            { error: "payment required" },
            402,
            challenge === undefined
              ? {}
              : {
                  "PAYMENT-REQUIRED": Buffer.from(
                    JSON.stringify(challenge),
                    "utf8",
                  ).toString("base64"),
                },
          );
        }
        return json({ error: "inference failed" }, 502);
      }
    }
    return json({ error: "not found" }, 404);
  }
}
