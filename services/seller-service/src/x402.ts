import type { Address } from "viem";

import type {
  PaymentAuthorization,
  PaymentGate,
  PaymentSettlement,
} from "./contracts.js";
import type { PaymentChallengeProvider } from "./http.js";

export interface X402QuoteTerms {
  modelId: string;
  amount: bigint;
  token: Address;
  payTo: Address;
  expiresAt: bigint;
}

export interface QuoteTermsReader {
  read(purchaseId: string, quoteId: string): Promise<X402QuoteTerms | null>;
}

export class Permit2ChallengeProvider implements PaymentChallengeProvider {
  readonly #quotes: QuoteTermsReader;
  readonly #providerId: string;

  constructor(quotes: QuoteTermsReader, providerId: string) {
    this.#quotes = quotes;
    this.#providerId = providerId;
  }

  async challenge(purchaseId: string, quoteId: string, resourceUrl: string) {
    const quote = await this.#quotes.read(purchaseId, quoteId);
    if (quote === null) throw new Error("unknown payment quote");
    const remaining = quote.expiresAt - BigInt(Math.floor(Date.now() / 1000));
    if (remaining <= 0n) throw new Error("payment quote expired");
    const maxTimeoutSeconds = Number(remaining < 60n ? remaining : 60n);
    return {
      x402Version: 2,
      error: "PAYMENT-SIGNATURE header is required",
      resource: {
        url: resourceUrl,
        description: `${this.#providerId} paid inference`,
        mimeType: "application/json",
        serviceName: `${this.#providerId} seller`.slice(0, 32),
        tags: ["ai-inference"],
      },
      accepts: [
        {
          scheme: "exact",
          network: "eip155:84532",
          amount: quote.amount.toString(),
          asset: quote.token,
          payTo: quote.payTo,
          maxTimeoutSeconds,
          extra: { assetTransferMethod: "permit2" },
        },
      ],
      extensions: {},
    };
  }
}

interface PaymentRequirements {
  scheme: string;
  network: string;
  amount: string;
  asset: Address;
  payTo: Address;
  maxTimeoutSeconds: number;
  extra?: Record<string, unknown>;
}

interface PaymentPayload {
  x402Version: 2;
  resource: { url: string };
  accepted: PaymentRequirements;
  payload: unknown;
  extensions?: Record<string, unknown>;
}

interface SettlementContext {
  paymentPayload: PaymentPayload;
  paymentRequirements: PaymentRequirements;
}

function decodePaymentSignature(encoded: string): PaymentPayload {
  let value: unknown;
  try {
    value = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  } catch {
    throw new Error("invalid PAYMENT-SIGNATURE header");
  }
  if (typeof value !== "object" || value === null) {
    throw new Error("invalid PAYMENT-SIGNATURE payload");
  }
  const payload = value as Partial<PaymentPayload>;
  if (
    payload.x402Version !== 2 ||
    typeof payload.resource?.url !== "string" ||
    typeof payload.accepted !== "object" ||
    payload.accepted === null ||
    payload.payload === undefined
  ) {
    throw new Error("invalid PAYMENT-SIGNATURE payload");
  }
  return payload as PaymentPayload;
}

function requirementFor(quote: X402QuoteTerms, accepted: PaymentRequirements) {
  const remaining = quote.expiresAt - BigInt(Math.floor(Date.now() / 1000));
  if (remaining <= 0n) throw new Error("payment quote expired");
  const requirement: PaymentRequirements = {
    scheme: "exact",
    network: "eip155:84532",
    amount: quote.amount.toString(),
    asset: quote.token,
    payTo: quote.payTo,
    maxTimeoutSeconds: 60,
    extra: { assetTransferMethod: "permit2" },
  };
  if (
    accepted.scheme !== requirement.scheme ||
    accepted.network !== requirement.network ||
    accepted.amount !== requirement.amount ||
    accepted.asset.toLowerCase() !== requirement.asset.toLowerCase() ||
    accepted.payTo.toLowerCase() !== requirement.payTo.toLowerCase() ||
    !Number.isSafeInteger(accepted.maxTimeoutSeconds) ||
    accepted.maxTimeoutSeconds <= 0 ||
    accepted.maxTimeoutSeconds > requirement.maxTimeoutSeconds ||
    accepted.extra?.assetTransferMethod !== "permit2"
  ) {
    throw new Error("PAYMENT-SIGNATURE does not match the signed quote");
  }
  return accepted;
}

async function facilitatorJson(
  fetchImpl: typeof fetch,
  url: string,
  headers: Record<string, string>,
  body: unknown,
): Promise<Record<string, unknown>> {
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const value = await response.json() as unknown;
  if (!response.ok || typeof value !== "object" || value === null) {
    throw new Error(`facilitator ${response.status} response is invalid`);
  }
  return value as Record<string, unknown>;
}

export class FacilitatorPaymentGate implements PaymentGate {
  readonly #quotes: QuoteTermsReader;
  readonly #facilitatorUrl: string;
  readonly #headers: Record<string, string>;
  readonly #fetch: typeof fetch;

  constructor(args: {
    quotes: QuoteTermsReader;
    facilitatorUrl: string;
    facilitatorHeaders?: Record<string, string>;
    fetchImpl?: typeof fetch;
  }) {
    this.#quotes = args.quotes;
    this.#facilitatorUrl = args.facilitatorUrl.replace(/\/$/, "");
    this.#headers = { ...args.facilitatorHeaders };
    this.#fetch = args.fetchImpl ?? fetch;
  }

  async authorize(proof: {
    purchaseId: string;
    quoteId: string;
    paymentSignature?: string;
  }): Promise<PaymentAuthorization> {
    if (!proof.paymentSignature) throw new Error("payment required");
    const quote = await this.#quotes.read(proof.purchaseId, proof.quoteId);
    if (quote === null) throw new Error("unknown payment quote");
    const paymentPayload = decodePaymentSignature(proof.paymentSignature);
    const paymentRequirements = requirementFor(quote, paymentPayload.accepted);
    const verified = await facilitatorJson(
      this.#fetch,
      `${this.#facilitatorUrl}/verify`,
      this.#headers,
      { paymentPayload, paymentRequirements },
    );
    if (verified.isValid !== true || typeof verified.payer !== "string") {
      throw new Error(
        typeof verified.invalidReason === "string"
          ? verified.invalidReason
          : "facilitator rejected payment",
      );
    }
    return {
      purchaseId: proof.purchaseId,
      quoteId: proof.quoteId,
      modelId: quote.modelId,
      settlementContext: { paymentPayload, paymentRequirements } satisfies SettlementContext,
    };
  }

  async settle(authorization: PaymentAuthorization): Promise<PaymentSettlement> {
    const context = authorization.settlementContext as SettlementContext;
    if (!context?.paymentPayload || !context.paymentRequirements) {
      throw new Error("payment authorization settlement context is missing");
    }
    const settled = await facilitatorJson(
      this.#fetch,
      `${this.#facilitatorUrl}/settle`,
      this.#headers,
      context,
    );
    if (
      typeof settled.success !== "boolean" ||
      typeof settled.transaction !== "string" ||
      typeof settled.network !== "string"
    ) {
      throw new Error("facilitator settlement response is malformed");
    }
    return settled as unknown as PaymentSettlement;
  }
}
