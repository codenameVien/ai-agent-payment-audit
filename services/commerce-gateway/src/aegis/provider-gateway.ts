/**
 * A Provider Gateway: ordinary code in front of one Mock provider.
 *
 * It never trusts the caller. The amount, the token, the recipient and the model version
 * are re-derived from the immutable decision evidence it fetches itself, so a client that
 * asks to pay less, to pay someone else, or to be served a different model is refused
 * rather than negotiated with. The provider result is produced only after the Facilitator
 * reports a successful settlement.
 */

import {
  AegisEvidenceClient,
  EvidenceApiError,
  type AegisPaymentIntentView,
} from "./evidence-client.js";
import { MOCK_EXECUTION_MODE, type MockProvider } from "./providers.js";
import { authorizationDigest } from "./signer.js";
import { deriveTerms, AegisTermsError, type AegisTerms } from "./terms.js";
import {
  bindingFor,
  decodeAegisPaymentPayload,
  encodeHeader,
  requirementsFor,
  X402BindingError,
  type AegisPaymentPayload,
  type AegisPaymentRequired,
  type AegisPaymentRequirements,
  type AegisSettlementResponse,
} from "./x402.js";

export const DEFAULT_MAX_TIMEOUT_SECONDS = 60;
const CHAIN_ID = /^eip155:([0-9]+)$/;

function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/**
 * Why a Facilitator success cannot be accepted as the settlement of this request.
 *
 * A success answer is only evidence for the request it answers. An unattributed payer, a
 * different network or a different amount are all reasons to refuse, and none of them may
 * be repaired locally by substituting the value we expected.
 */
export function settlementMismatch(
  settled: AegisSettlementResponse,
  context: {
    paymentPayload: AegisPaymentPayload;
    paymentRequirements: AegisPaymentRequirements;
  },
): string | null {
  const expectedPayer = context.paymentPayload.payload.authorization.from.toLowerCase();
  if (typeof settled.payer !== "string" || settled.payer.length === 0) {
    return "facilitator settlement does not name the payer it debited";
  }
  if (settled.payer.toLowerCase() !== expectedPayer) {
    return "facilitator settlement names a different payer than the authorization";
  }
  if (settled.network !== context.paymentRequirements.network) {
    return "facilitator settlement is on a different network than the requirements";
  }
  if (settled.amount !== undefined && settled.amount !== context.paymentRequirements.amount) {
    return "facilitator settlement amount is not the required amount";
  }
  if (typeof settled.transaction !== "string" || settled.transaction.length === 0) {
    return "facilitator settlement carries no reference";
  }
  return null;
}

export interface ProviderGatewayOptions {
  providerId: string;
  provider: MockProvider;
  evidence: AegisEvidenceClient;
  facilitatorUrl: string;
  network: string;
  fetchImpl?: typeof fetch;
  maxTimeoutSeconds?: number;
  tokenName?: string;
  tokenVersion?: string;
}

export class ProviderGateway {
  readonly #providerId: string;
  readonly #provider: MockProvider;
  readonly #evidence: AegisEvidenceClient;
  readonly #facilitatorUrl: string;
  readonly #network: string;
  readonly #fetch: typeof fetch;
  readonly #maxTimeoutSeconds: number;
  readonly #tokenName: string | undefined;
  readonly #tokenVersion: string | undefined;
  /**
   * Settlements obtained by this process.
   *
   * This is a latency cache, never the authority. The durable payment intent in the
   * Evidence API decides whether a purchase may still be settled, so a restart of this
   * process cannot produce a second payment.
   */
  readonly #settlements = new Map<string, AegisSettlementResponse>();

  constructor(options: ProviderGatewayOptions) {
    this.#providerId = options.providerId;
    this.#provider = options.provider;
    this.#evidence = options.evidence;
    this.#facilitatorUrl = options.facilitatorUrl.replace(/\/$/, "");
    this.#network = options.network;
    this.#fetch = options.fetchImpl ?? fetch;
    this.#maxTimeoutSeconds = options.maxTimeoutSeconds ?? DEFAULT_MAX_TIMEOUT_SECONDS;
    this.#tokenName = options.tokenName;
    this.#tokenVersion = options.tokenVersion;
  }

  /** The gateway's own view of the terms, taken from evidence and never from the body. */
  async #terms(purchaseId: string): Promise<AegisTerms> {
    const terms = deriveTerms(await this.#evidence.decision(purchaseId));
    if (terms.providerId !== this.#providerId) {
      throw new AegisTermsError(
        `purchase selected ${terms.providerId}, not ${this.#providerId}`,
      );
    }
    if (
      terms.providerModelId !== this.#provider.providerModelId ||
      terms.modelVersion !== this.#provider.modelVersion
    ) {
      throw new AegisTermsError("purchase selected a model version this gateway does not serve");
    }
    return terms;
  }

  #requirements(terms: AegisTerms): AegisPaymentRequirements {
    return requirementsFor({
      terms,
      network: this.#network,
      maxTimeoutSeconds: this.#maxTimeoutSeconds,
      tokenName: this.#tokenName,
      tokenVersion: this.#tokenVersion,
    });
  }

  #chainId(): number {
    const match = CHAIN_ID.exec(this.#network);
    if (match === null) throw new Error(`unsupported network: ${this.#network}`);
    return Number(match[1]);
  }

  /**
   * Bind the presented payment to the one attempt the Evidence API durably reserved.
   *
   * Matching the decided amount and recipient is not enough: without this, any
   * well-formed authorization would do, and two different nonces for one purchase would
   * each settle. The nonce, the payer, the value, the recipient and the EIP-712 digest
   * must all be the reserved ones, and the reservation must still be awaiting settlement.
   */
  #assertReserved(
    payload: AegisPaymentPayload,
    requirement: AegisPaymentRequirements,
    terms: AegisTerms,
    intent: AegisPaymentIntentView,
  ): void {
    const authorization = payload.payload.authorization;
    if (intent.termsBindingHash !== terms.termsBindingHash) {
      throw new X402BindingError("the reserved attempt is bound to different terms");
    }
    if (authorization.nonce.toLowerCase() !== intent.authorizationNonce.toLowerCase()) {
      throw new X402BindingError(
        "the authorization nonce is not the nonce reserved for this purchase",
      );
    }
    if (authorization.from.toLowerCase() !== intent.buyerWalletAddress.toLowerCase()) {
      throw new X402BindingError("the payer is not the reserved buyer wallet");
    }
    if (authorization.value !== String(intent.amountUnits)) {
      throw new X402BindingError("the authorized value is not the reserved amount");
    }
    if (authorization.to.toLowerCase() !== intent.payTo.toLowerCase()) {
      throw new X402BindingError("the authorized recipient is not the reserved recipient");
    }
    const digest = authorizationDigest({
      token: requirement.asset as `0x${string}`,
      tokenName: requirement.extra.name,
      tokenVersion: requirement.extra.version,
      chainId: this.#chainId(),
      authorization: {
        from: authorization.from as `0x${string}`,
        to: authorization.to as `0x${string}`,
        value: authorization.value,
        validAfter: authorization.validAfter,
        validBefore: authorization.validBefore,
        nonce: authorization.nonce as `0x${string}`,
      },
    });
    if (intent.decisionAuthorizationHash === null) {
      throw new X402BindingError("this attempt has no recorded authorization to settle");
    }
    if (intent.decisionAuthorizationHash.toLowerCase() !== digest.toLowerCase()) {
      throw new X402BindingError(
        "the presented authorization is not the one recorded for this attempt",
      );
    }
  }

  #challenge(terms: AegisTerms, resourceUrl: string): AegisPaymentRequired {
    return {
      x402Version: 2,
      error: "PAYMENT-SIGNATURE header is required",
      resource: {
        url: resourceUrl,
        description: `${this.#providerId} ${this.#provider.providerModelId} mock inference`,
        mimeType: "application/json",
      },
      accepts: [this.#requirements(terms)],
      extensions: { aegis: bindingFor(terms, MOCK_EXECUTION_MODE) },
    };
  }

  /** The offered terms must be reproduced field for field; there is no tolerance. */
  #assertPaid(payload: AegisPaymentPayload, terms: AegisTerms): AegisPaymentRequirements {
    const expected = this.#requirements(terms);
    const accepted = payload.accepted;
    if (
      accepted.scheme !== expected.scheme ||
      accepted.network !== expected.network ||
      accepted.amount !== expected.amount ||
      typeof accepted.asset !== "string" ||
      accepted.asset.toLowerCase() !== expected.asset ||
      typeof accepted.payTo !== "string" ||
      accepted.payTo.toLowerCase() !== expected.payTo ||
      !Number.isSafeInteger(accepted.maxTimeoutSeconds) ||
      accepted.maxTimeoutSeconds <= 0 ||
      accepted.maxTimeoutSeconds > expected.maxTimeoutSeconds ||
      accepted.extra?.assetTransferMethod !== "eip3009" ||
      accepted.extra?.name !== expected.extra.name ||
      accepted.extra?.version !== expected.extra.version
    ) {
      throw new X402BindingError("PAYMENT-SIGNATURE does not match the decided terms");
    }
    const binding = bindingFor(terms, MOCK_EXECUTION_MODE);
    const offered = payload.extensions?.aegis;
    if (offered === undefined) {
      throw new X402BindingError("PAYMENT-SIGNATURE carries no AEGIS decision binding");
    }
    for (const key of Object.keys(binding) as (keyof typeof binding)[]) {
      if (offered[key] !== binding[key]) {
        throw new X402BindingError(`PAYMENT-SIGNATURE binding field ${key} is not the decided value`);
      }
    }
    return { ...expected, maxTimeoutSeconds: accepted.maxTimeoutSeconds };
  }

  async #facilitator<T>(path: string, body: unknown): Promise<T> {
    const response = await this.#fetch(`${this.#facilitatorUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`facilitator ${response.status}: ${text.slice(0, 240)}`);
    }
    return JSON.parse(text) as T;
  }

  async #settle(
    purchaseId: string,
    context: { paymentPayload: AegisPaymentPayload; paymentRequirements: AegisPaymentRequirements },
  ): Promise<AegisSettlementResponse> {
    const known = this.#settlements.get(purchaseId);
    if (known !== undefined) return known;
    const verified = await this.#facilitator<{
      isValid: boolean;
      payer?: string;
      invalidReason?: string;
    }>("/verify", context);
    if (verified.isValid !== true || typeof verified.payer !== "string") {
      return {
        success: false,
        transaction: "",
        network: context.paymentRequirements.network,
        errorReason: verified.invalidReason ?? "facilitator rejected the payment",
      };
    }
    const settled = await this.#facilitator<AegisSettlementResponse>("/settle", context);
    if (settled.success !== true) return settled;
    const refusal = settlementMismatch(settled, context);
    if (refusal !== null) {
      // A success we cannot match to what we asked for is not a settlement we will serve
      // a result against, and the original answer is preserved in the reason.
      return {
        ...settled,
        success: false,
        errorReason: refusal,
      };
    }
    this.#settlements.set(purchaseId, settled);
    return settled;
  }

  async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        status: "ok",
        providerId: this.#providerId,
        providerModelId: this.#provider.providerModelId,
        modelVersion: this.#provider.modelVersion,
        executionMode: MOCK_EXECUTION_MODE,
      });
    }
    if (request.method !== "POST" || url.pathname !== "/v1/inference") {
      return json({ error: "not found" }, 404);
    }
    const purchaseId = url.searchParams.get("purchaseId") ?? "";
    let prompt: string;
    try {
      const body = (await request.json()) as { prompt?: unknown };
      if (!purchaseId || typeof body.prompt !== "string" || body.prompt.length === 0) {
        return json({ error: "invalid inference request" }, 400);
      }
      prompt = body.prompt;
    } catch {
      return json({ error: "invalid inference request" }, 400);
    }

    let terms: AegisTerms;
    try {
      terms = await this.#terms(purchaseId);
    } catch (error) {
      if (error instanceof EvidenceApiError) {
        return json({ error: error.message }, error.status === 404 ? 404 : 409);
      }
      if (error instanceof AegisTermsError) return json({ error: error.message }, 409);
      throw error;
    }

    const header = request.headers.get("PAYMENT-SIGNATURE");
    let intent: AegisPaymentIntentView | null;
    try {
      intent = await this.#evidence.paymentIntent(purchaseId);
    } catch (error) {
      if (error instanceof EvidenceApiError) return json({ error: error.message }, 409);
      throw error;
    }

    // A purchase that is already paid is delivered, not charged again. This is durable,
    // so it also holds after this process or the Facilitator has been restarted.
    if (intent?.state === "SETTLED") {
      try {
        return json(await this.#provider.complete({ purchaseId, prompt }), 200);
      } catch (error) {
        return json(
          {
            error: error instanceof Error ? error.message : "provider execution failed",
            settled: true,
          },
          502,
        );
      }
    }
    if (header === null) {
      return json({ error: "payment required" }, 402, {
        "PAYMENT-REQUIRED": encodeHeader(this.#challenge(terms, request.url)),
      });
    }
    if (intent === null) {
      return json({ error: "this purchase has no reserved payment attempt" }, 402, {
        "PAYMENT-REQUIRED": encodeHeader(this.#challenge(terms, request.url)),
      });
    }
    if (intent.state !== "AUTHORIZED") {
      return json(
        { error: `the reserved attempt is ${intent.state}, not awaiting settlement` },
        402,
      );
    }

    let context: {
      paymentPayload: AegisPaymentPayload;
      paymentRequirements: AegisPaymentRequirements;
    };
    try {
      const paymentPayload = decodeAegisPaymentPayload(header);
      const paymentRequirements = this.#assertPaid(paymentPayload, terms);
      this.#assertReserved(paymentPayload, paymentRequirements, terms, intent);
      context = { paymentPayload, paymentRequirements };
    } catch (error) {
      const message = error instanceof Error ? error.message : "invalid payment";
      return json({ error: message }, 402, {
        "PAYMENT-REQUIRED": encodeHeader(this.#challenge(terms, request.url)),
      });
    }

    const settlement = await this.#settle(purchaseId, context);
    const paymentResponse = { "PAYMENT-RESPONSE": encodeHeader(settlement) };
    if (settlement.success !== true) {
      return json(
        { error: settlement.errorReason ?? "payment settlement failed" },
        402,
        paymentResponse,
      );
    }
    try {
      const result = await this.#provider.complete({ purchaseId, prompt });
      return json(result, 200, paymentResponse);
    } catch (error) {
      // The payment is settled and stays settled. Only the delivery failed, and the
      // caller is told so explicitly instead of being invited to pay again.
      return json(
        {
          error: error instanceof Error ? error.message : "provider execution failed",
          settled: true,
        },
        502,
        paymentResponse,
      );
    }
  }
}
