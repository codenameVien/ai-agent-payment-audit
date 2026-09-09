/**
 * The payment execution module (결제 실행 모듈).
 *
 * It is the only process that holds a signing key, and it signs nothing it has not first
 * verified against evidence it fetched itself: the 402 challenge is compared field by
 * field with the terms derived from the immutable decision, and the amount it authorizes
 * is the amount the Evidence API reserved, not the amount the challenge asked for.
 *
 * One purchase gets one attempt. A settled, failed or ambiguous purchase is never paid
 * again: an unknown settlement outcome is parked for reconciliation instead of retried,
 * and a delivery failure after a successful settlement re-requests the result only.
 */

import { createHash } from "node:crypto";

import type { Address, Hex } from "viem";

import type { Erc3009Authorization } from "../contracts.js";
import {
  AegisEvidenceClient,
  EvidenceApiError,
  type AegisPaymentIntentView,
  type AegisPaymentTermsView,
} from "./evidence-client.js";
import { MOCK_EXECUTION_MODE } from "./providers.js";
import { AegisTermsError, deriveTerms, type AegisTerms } from "./terms.js";
import type { AegisAuthorizationSigner } from "./signer.js";
import {
  bindingFor,
  decodeAegisPaymentRequired,
  decodeAegisSettlement,
  encodeHeader,
  selectBoundRequirement,
  X402BindingError,
  type AegisPaymentPayload,
  type AegisSettlementResponse,
} from "./x402.js";

export const DEFAULT_AUTHORIZATION_WINDOW_SECONDS = 3600;
export const DEFAULT_GATEWAY_TIMEOUT_MS = 15_000;

export type AegisPaymentStatus =
  | "settled"
  | "settled_delivery_failed"
  | "failed"
  | "unknown"
  | "already_terminal";

export interface AegisExecutionResult {
  purchase_id: string;
  state: string;
  payment_status: AegisPaymentStatus;
  amount_units: number;
  token: string;
  pay_to: string;
  provider_id: string;
  provider_model_id: string;
  model_version: string;
  execution_mode: string;
  verification_basis: string;
  aa_mode: string;
  aa_mapping_provenance: string;
  settlement_reference: string | null;
  settlement_network: string | null;
  provider_result: unknown;
  provider_error: string | null;
}

export class PaymentExecutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PaymentExecutionError";
  }
}

interface GatewayResponse {
  status: number;
  paymentRequired?: string;
  paymentResponse?: string;
  body: unknown;
}

function sha256Text(value: string): string {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

function bodyError(body: unknown): string {
  if (typeof body === "object" && body !== null) {
    const error = (body as Record<string, unknown>).error;
    if (typeof error === "string" && error.length > 0) return error.slice(0, 240);
  }
  return "provider gateway refused the request";
}

/**
 * Why a Facilitator success answer does not match what this module asked to be paid.
 *
 * The Facilitator response is the payment status basis of this runtime, so it is compared
 * with the decided terms rather than assumed to describe them. Nothing missing from the
 * answer is filled in locally.
 */
export function settlementAnswerMismatch(
  settlement: AegisSettlementResponse,
  expected: { network: string; payer: string; amountUnits: bigint },
): string | null {
  if (settlement.network !== expected.network) {
    return `facilitator settled on ${settlement.network}, not the decided ${expected.network}`;
  }
  if ((settlement.payer ?? "").toLowerCase() !== expected.payer.toLowerCase()) {
    return "facilitator settlement names a payer this module did not authorize";
  }
  if (settlement.amount !== undefined && settlement.amount !== expected.amountUnits.toString()) {
    return `facilitator settled ${settlement.amount}, not the decided ${expected.amountUnits}`;
  }
  if (settlement.transaction.length === 0) {
    return "facilitator settlement carries no reference";
  }
  return null;
}

export interface AegisPaymentExecutorOptions {
  evidence: AegisEvidenceClient;
  signer: AegisAuthorizationSigner;
  gatewayRoutes: Record<string, string>;
  network: string;
  fetchImpl?: typeof fetch;
  executionMode?: string;
  authorizationWindowSeconds?: number;
  gatewayTimeoutMs?: number;
  nowSeconds?: () => number;
}

export class AegisPaymentExecutor {
  readonly #evidence: AegisEvidenceClient;
  readonly #signer: AegisAuthorizationSigner;
  readonly #routes: Record<string, string>;
  readonly #network: string;
  readonly #fetch: typeof fetch;
  readonly #executionMode: string;
  readonly #window: number;
  readonly #timeoutMs: number;
  readonly #nowSeconds: () => number;

  constructor(options: AegisPaymentExecutorOptions) {
    this.#evidence = options.evidence;
    this.#signer = options.signer;
    this.#routes = { ...options.gatewayRoutes };
    this.#network = options.network;
    this.#fetch = options.fetchImpl ?? fetch;
    this.#executionMode = options.executionMode ?? MOCK_EXECUTION_MODE;
    this.#window = options.authorizationWindowSeconds ?? DEFAULT_AUTHORIZATION_WINDOW_SECONDS;
    this.#timeoutMs = options.gatewayTimeoutMs ?? DEFAULT_GATEWAY_TIMEOUT_MS;
    this.#nowSeconds = options.nowSeconds ?? (() => Math.floor(Date.now() / 1000));
  }

  get buyerAddress(): Address {
    return this.#signer.address;
  }

  #route(providerId: string): string {
    const url = this.#routes[providerId];
    if (url === undefined) {
      throw new PaymentExecutionError(`no Provider Gateway route for ${providerId}`);
    }
    return url.replace(/\/$/, "");
  }

  async #call(
    providerId: string,
    purchaseId: string,
    resourceBody: unknown,
    paymentSignature?: string,
  ): Promise<GatewayResponse> {
    const url = new URL(`${this.#route(providerId)}/v1/inference`);
    url.searchParams.set("purchaseId", purchaseId);
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (paymentSignature !== undefined) headers["PAYMENT-SIGNATURE"] = paymentSignature;
    const response = await this.#fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(resourceBody ?? {}),
      signal: AbortSignal.timeout(this.#timeoutMs),
    });
    const text = await response.text();
    let body: unknown;
    try {
      body = text.length === 0 ? undefined : JSON.parse(text);
    } catch {
      body = text;
    }
    return {
      status: response.status,
      paymentRequired: response.headers.get("PAYMENT-REQUIRED") ?? undefined,
      paymentResponse: response.headers.get("PAYMENT-RESPONSE") ?? undefined,
      body,
    };
  }

  /** The Evidence API and this module must agree before anything is signed. */
  #assertServerAgrees(terms: AegisPaymentTermsView, derived: AegisTerms): void {
    if (
      BigInt(terms.amountUnits) !== derived.amountUnits ||
      terms.recipient.toLowerCase() !== derived.recipient ||
      terms.providerId !== derived.providerId ||
      terms.providerModelId !== derived.providerModelId ||
      terms.modelVersion !== derived.modelVersion ||
      terms.snapshotHash !== derived.snapshotHash ||
      terms.decisionEventHash !== derived.decisionEventHash ||
      terms.termsBindingHash !== derived.termsBindingHash
    ) {
      throw new AegisTermsError(
        "the Evidence API terms are not the terms this module derived from the decision",
      );
    }
    if (derived.amountUnits > BigInt(terms.budgetUnits)) {
      throw new AegisTermsError("decided amount exceeds the recorded request budget");
    }
  }

  #authorization(intent: AegisPaymentIntentView, derived: AegisTerms): Erc3009Authorization {
    if (intent.buyerWalletAddress.toLowerCase() !== this.#signer.address.toLowerCase()) {
      throw new PaymentExecutionError(
        "the reserved buyer wallet is not the address this module can sign for",
      );
    }
    if (BigInt(intent.amountUnits) !== derived.amountUnits) {
      throw new PaymentExecutionError("the reserved amount is not the decided amount");
    }
    // Deterministic window: a retry of the same reservation reproduces the same
    // authorization instead of creating a second payable one.
    const claimedAt = Math.floor(Date.parse(intent.claimedAt) / 1000);
    if (!Number.isSafeInteger(claimedAt)) {
      throw new PaymentExecutionError("the reservation timestamp is malformed");
    }
    return {
      from: this.#signer.address,
      to: derived.recipient as Address,
      value: derived.amountUnits.toString(),
      validAfter: String(claimedAt - 1),
      validBefore: String(claimedAt + this.#window),
      nonce: intent.authorizationNonce as Hex,
    };
  }

  #result(args: {
    derived: AegisTerms;
    intent: AegisPaymentIntentView;
    status: AegisPaymentStatus;
    settlement?: AegisSettlementResponse;
    providerResult?: unknown;
    providerError?: string;
  }): AegisExecutionResult {
    return {
      purchase_id: args.derived.purchaseId,
      state: args.intent.state,
      payment_status: args.status,
      amount_units: args.intent.amountUnits,
      token: args.intent.token,
      pay_to: args.intent.payTo,
      provider_id: args.derived.providerId,
      provider_model_id: args.derived.providerModelId,
      model_version: args.derived.modelVersion,
      execution_mode: this.#executionMode,
      verification_basis: "facilitator_response",
      aa_mode: args.derived.aaMode,
      aa_mapping_provenance: args.derived.aaMappingProvenance,
      settlement_reference: args.settlement?.transaction ?? null,
      settlement_network: args.settlement?.network ?? null,
      provider_result: args.providerResult ?? null,
      provider_error: args.providerError ?? null,
    };
  }

  async execute(args: {
    purchaseId: string;
    resourceBody: unknown;
  }): Promise<AegisExecutionResult> {
    const derived = deriveTerms(await this.#evidence.decision(args.purchaseId));
    const reservation = await this.#evidence.reserve(args.purchaseId);
    this.#assertServerAgrees(reservation.terms, derived);
    let intent = reservation.intent;
    if (intent.termsBindingHash !== derived.termsBindingHash) {
      throw new AegisTermsError("the reservation is bound to different terms");
    }
    if (intent.state === "FAILED" || intent.state === "MISMATCH_CONFIRMED") {
      return this.#result({ derived, intent, status: "already_terminal" });
    }
    if (intent.state === "RECONCILED_NO_TRANSFER") {
      return this.#result({ derived, intent, status: "already_terminal" });
    }
    if (intent.state === "RECONCILIATION_REQUIRED") {
      // The previous attempt's outcome is unknown. A new payment is never the answer.
      return this.#result({ derived, intent, status: "unknown" });
    }
    if (intent.state === "SETTLED") {
      // Already paid. Recover the result without signing, verifying or settling again;
      // the gateway serves it from the same durable settlement evidence.
      return await this.#recoverDelivered(args, derived, intent);
    }

    const challenge = await this.#call(derived.providerId, args.purchaseId, args.resourceBody);
    if (challenge.status !== 402 || challenge.paymentRequired === undefined) {
      throw new PaymentExecutionError(
        `provider gateway did not issue an x402 challenge: ${challenge.status} ${bodyError(
          challenge.body,
        )}`,
      );
    }
    const required = decodeAegisPaymentRequired(challenge.paymentRequired);
    const requirement = selectBoundRequirement({
      required,
      terms: derived,
      network: this.#network,
      executionMode: this.#executionMode,
    });
    const authorization = this.#authorization(intent, derived);
    const now = this.#nowSeconds();
    if (BigInt(authorization.validBefore) <= BigInt(now)) {
      throw new PaymentExecutionError(
        "the reserved authorization window has expired; this attempt needs reconciliation",
      );
    }
    const signed = await this.#signer.sign({
      token: derived.token.address as Address,
      tokenName: requirement.extra.name,
      tokenVersion: requirement.extra.version,
      authorization,
    });
    if (intent.state === "CLAIMED") {
      intent = await this.#evidence.authorize({
        purchaseId: args.purchaseId,
        authorizationHash: signed.hash,
        signature: signed.signature,
      });
    }
    const payload: AegisPaymentPayload = {
      x402Version: 2,
      resource: required.resource,
      accepted: requirement,
      payload: { signature: signed.signature, authorization },
      extensions: { aegis: bindingFor(derived, this.#executionMode) },
    };

    let paid: GatewayResponse;
    try {
      paid = await this.#call(
        derived.providerId,
        args.purchaseId,
        args.resourceBody,
        encodeHeader(payload),
      );
    } catch (error) {
      const reason = error instanceof Error ? error.message : "provider gateway call failed";
      intent = await this.#evidence.requireReconciliation(
        args.purchaseId,
        `facilitator settlement outcome unknown: ${reason}`.slice(0, 200),
      );
      return this.#result({ derived, intent, status: "unknown" });
    }

    let settlement: AegisSettlementResponse | undefined;
    if (paid.paymentResponse !== undefined) {
      try {
        settlement = decodeAegisSettlement(paid.paymentResponse);
      } catch (error) {
        if (!(error instanceof X402BindingError)) throw error;
        settlement = undefined;
      }
    }

    if (settlement?.success === true) {
      const payer = settlement.payer;
      if (typeof payer !== "string" || payer.length === 0) {
        // An unattributed success is not proof this purchase was debited, and inventing
        // the payer we expected would record an attribution nobody verified.
        intent = await this.#evidence.requireReconciliation(
          args.purchaseId,
          "facilitator settlement did not name the payer it debited",
        );
        return this.#result({
          derived,
          intent,
          status: "unknown",
          settlement,
          providerError: "facilitator settlement did not name the payer it debited",
        });
      }
      const mismatch = settlementAnswerMismatch(settlement, {
        network: this.#network,
        payer: this.#signer.address,
        amountUnits: derived.amountUnits,
      });
      if (mismatch !== null) {
        intent = await this.#evidence.requireReconciliation(args.purchaseId, mismatch);
        return this.#result({
          derived,
          intent,
          status: "unknown",
          settlement,
          providerError: mismatch,
        });
      }
      intent = await this.#evidence.settle({
        purchaseId: args.purchaseId,
        facilitatorTransaction: settlement.transaction,
        facilitatorNetwork: settlement.network,
        facilitatorPayer: payer,
      });
      if (paid.status !== 200) {
        // Paid, not delivered. The payment stays settled and is never repeated.
        return this.#result({
          derived,
          intent,
          status: "settled_delivery_failed",
          settlement,
          providerError: bodyError(paid.body),
        });
      }
      const providerResult = paid.body;
      await this.#evidence.recordDelivery({
        purchaseId: args.purchaseId,
        providerId: derived.providerId,
        providerModelId: derived.providerModelId,
        modelVersion: derived.modelVersion,
        responseId: this.#responseId(providerResult),
        responseHash: sha256Text(JSON.stringify(providerResult)),
        observedExecutionMs: this.#observedMs(providerResult),
      });
      return this.#result({
        derived,
        intent,
        status: "settled",
        settlement,
        providerResult,
      });
    }

    if (paid.status === 402) {
      // An explicit payment refusal. Releasing the reservation is safe precisely because
      // the Facilitator never reported a successful settlement for it.
      intent = await this.#evidence.fail({
        purchaseId: args.purchaseId,
        reason: (settlement?.errorReason ?? bodyError(paid.body)).slice(0, 200),
      });
      return this.#result({
        derived,
        intent,
        status: "failed",
        providerError: settlement?.errorReason ?? bodyError(paid.body),
      });
    }

    intent = await this.#evidence.requireReconciliation(
      args.purchaseId,
      `provider gateway answered ${paid.status} without a settlement answer`,
    );
    return this.#result({
      derived,
      intent,
      status: "unknown",
      providerError: bodyError(paid.body),
    });
  }

  #responseId(result: unknown): string {
    if (typeof result === "object" && result !== null) {
      const value = (result as Record<string, unknown>).responseId;
      if (typeof value === "string" && value.length > 0) return value;
    }
    throw new PaymentExecutionError("provider result has no responseId");
  }

  /**
   * Deliver an already settled purchase.
   *
   * No signature is produced and no Facilitator call is made: the gateway recognises the
   * durable settlement and returns the result, so this path is safe after a restart of
   * any process, including one that lost its in-memory settlement cache.
   */
  async #recoverDelivered(
    args: { purchaseId: string; resourceBody: unknown },
    derived: AegisTerms,
    intent: AegisPaymentIntentView,
  ): Promise<AegisExecutionResult> {
    let delivered: GatewayResponse;
    try {
      delivered = await this.#call(derived.providerId, args.purchaseId, args.resourceBody);
    } catch (error) {
      return this.#result({
        derived,
        intent,
        status: "settled_delivery_failed",
        providerError: error instanceof Error ? error.message : "provider gateway call failed",
      });
    }
    if (delivered.status !== 200) {
      return this.#result({
        derived,
        intent,
        status: "settled_delivery_failed",
        providerError: bodyError(delivered.body),
      });
    }
    await this.#evidence.recordDelivery({
      purchaseId: args.purchaseId,
      providerId: derived.providerId,
      providerModelId: derived.providerModelId,
      modelVersion: derived.modelVersion,
      responseId: this.#responseId(delivered.body),
      responseHash: sha256Text(JSON.stringify(delivered.body)),
      observedExecutionMs: this.#observedMs(delivered.body),
    });
    return this.#result({
      derived,
      intent,
      status: "settled",
      providerResult: delivered.body,
    });
  }

  #observedMs(result: unknown): number {
    if (typeof result === "object" && result !== null) {
      const value = (result as Record<string, unknown>).observedExecutionMs;
      if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
    }
    return 0;
  }
}

export { EvidenceApiError };
