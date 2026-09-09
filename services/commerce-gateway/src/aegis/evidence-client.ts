/**
 * The only way the AEGIS runtime touches stored evidence.
 *
 * Neither the Provider Gateway nor the payment execution module opens a MongoDB
 * connection; the internal Evidence API owns every record, every append-only ordering
 * rule and the budget reservation. This client is deliberately narrow: it can read the
 * fixed decision and move one purchase through its single payment attempt, and it has no
 * operation that could write an amount the Evidence API did not derive itself.
 */

import { AegisTermsError, type AegisDecisionEvidence } from "./terms.js";

export class EvidenceApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "EvidenceApiError";
    this.status = status;
  }
}

export interface AegisPaymentTermsView {
  purchaseId: string;
  amountUnits: number;
  budgetUnits: number;
  decisionEventHash: string;
  snapshotHash: string;
  termsBindingHash: string;
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  recipient: string;
}

export interface AegisPaymentIntentView {
  purchaseId: string;
  buyerWalletAddress: string;
  state: string;
  amountUnits: number;
  token: string;
  payTo: string;
  authorizationNonce: string;
  /** The EIP-712 digest recorded when this attempt was authorized, if it already was. */
  decisionAuthorizationHash: string | null;
  claimedAt: string;
  termsBindingHash: string;
}

interface RawTerms {
  purchase_id: string;
  amount_units: number;
  budget_units: number;
  decision_event_hash: string;
  snapshot_hash: string;
  terms_binding_hash: string;
  provider_id: string;
  provider_model_id: string;
  model_version: string;
  recipient: string;
}

interface RawIntent {
  purchase_id: string;
  buyer_wallet_address: string;
  state: string;
  amount_units: number;
  token: string;
  pay_to: string;
  authorization_nonce: string | null;
  decision_authorization_hash?: string | null;
  claimed_at: string;
  quote_id: string;
}

/**
 * JSON numbers cross this boundary as IEEE-754 doubles.
 *
 * A unit amount above 2^53 would arrive already rounded, and rounding then converting to
 * BigInt would sign an amount nobody decided. Every unit count is therefore refused
 * unless it survived the wire exactly.
 */
function exactUnits(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new AegisTermsError(`${field} is not an exact non-negative integer on the wire`);
  }
  return value;
}

function toTerms(raw: RawTerms): AegisPaymentTermsView {
  return {
    purchaseId: raw.purchase_id,
    amountUnits: exactUnits(raw.amount_units, "payment terms amountUnits"),
    budgetUnits: exactUnits(raw.budget_units, "payment terms budgetUnits"),
    decisionEventHash: raw.decision_event_hash,
    snapshotHash: raw.snapshot_hash,
    termsBindingHash: raw.terms_binding_hash,
    providerId: raw.provider_id,
    providerModelId: raw.provider_model_id,
    modelVersion: raw.model_version,
    recipient: raw.recipient,
  };
}

function toIntent(raw: RawIntent): AegisPaymentIntentView {
  return {
    purchaseId: raw.purchase_id,
    buyerWalletAddress: raw.buyer_wallet_address,
    state: raw.state,
    amountUnits: exactUnits(raw.amount_units, "payment intent amountUnits"),
    token: raw.token,
    payTo: raw.pay_to,
    authorizationNonce: raw.authorization_nonce ?? "",
    decisionAuthorizationHash: raw.decision_authorization_hash ?? null,
    claimedAt: raw.claimed_at,
    // The new runtime has no negotiated quote: this identity is the terms binding.
    termsBindingHash: raw.quote_id,
  };
}

export class AegisEvidenceClient {
  readonly #baseUrl: string;
  readonly #authorization: string;
  readonly #fetch: typeof fetch;

  constructor(args: {
    baseUrl: string;
    internalServiceToken: string;
    fetchImpl?: typeof fetch;
  }) {
    this.#baseUrl = args.baseUrl.replace(/\/$/, "");
    this.#authorization = `Bearer ${args.internalServiceToken}`;
    this.#fetch = args.fetchImpl ?? fetch;
  }

  async #request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.#fetch(`${this.#baseUrl}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        authorization: this.#authorization,
        ...init?.headers,
      },
    });
    const text = await response.text();
    if (!response.ok) {
      throw new EvidenceApiError(
        response.status,
        `internal evidence API ${response.status}: ${text.slice(0, 300)}`,
      );
    }
    return JSON.parse(text) as T;
  }

  #post<T>(path: string, body: unknown): Promise<T> {
    return this.#request<T>(path, { method: "POST", body: JSON.stringify(body) });
  }

  async decision(purchaseId: string): Promise<AegisDecisionEvidence> {
    const raw = await this.#request<{
      purchase_id: string;
      decision: Record<string, unknown>;
      decision_event_hash: string;
      snapshot: Record<string, unknown>;
      snapshot_event_hash: string;
      snapshot_hash: string;
    }>(`/internal/purchases/${encodeURIComponent(purchaseId)}/aegis-decision`);
    return {
      purchaseId: raw.purchase_id,
      decision: raw.decision,
      decisionEventHash: raw.decision_event_hash,
      snapshot: raw.snapshot,
      snapshotEventHash: raw.snapshot_event_hash,
      snapshotHash: raw.snapshot_hash,
    };
  }

  async paymentTerms(purchaseId: string): Promise<AegisPaymentTermsView> {
    return toTerms(
      await this.#request<RawTerms>(
        `/internal/evidence/aegis/purchases/${encodeURIComponent(purchaseId)}/payment-terms`,
      ),
    );
  }

  async reserve(
    purchaseId: string,
  ): Promise<{ terms: AegisPaymentTermsView; intent: AegisPaymentIntentView }> {
    const raw = await this.#post<{ terms: RawTerms; intent: RawIntent }>(
      "/internal/evidence/aegis/payments/reserve",
      { purchase_id: purchaseId },
    );
    return { terms: toTerms(raw.terms), intent: toIntent(raw.intent) };
  }

  async paymentIntent(purchaseId: string): Promise<AegisPaymentIntentView | null> {
    try {
      return toIntent(
        await this.#request<RawIntent>(
          `/internal/evidence/payment-intents/${encodeURIComponent(purchaseId)}`,
        ),
      );
    } catch (error) {
      if (error instanceof EvidenceApiError && error.status === 404) return null;
      throw error;
    }
  }

  async authorize(args: {
    purchaseId: string;
    authorizationHash: string;
    signature: string;
  }): Promise<AegisPaymentIntentView> {
    return toIntent(
      await this.#post<RawIntent>("/internal/evidence/payment-intents/authorize", {
        purchase_id: args.purchaseId,
        authorization_hash: args.authorizationHash,
        signature: args.signature,
      }),
    );
  }

  async settle(args: {
    purchaseId: string;
    facilitatorTransaction: string;
    facilitatorNetwork: string;
    facilitatorPayer: string;
    facilitatorAmount?: string;
  }): Promise<AegisPaymentIntentView> {
    return toIntent(
      await this.#post<RawIntent>("/internal/evidence/aegis/payments/settle", {
        purchase_id: args.purchaseId,
        facilitator_transaction: args.facilitatorTransaction,
        facilitator_network: args.facilitatorNetwork,
        facilitator_payer: args.facilitatorPayer,
        facilitator_amount: args.facilitatorAmount ?? null,
      }),
    );
  }

  /**
   * Park a Facilitator success that contradicts the terms it answers.
   *
   * The original answer is stored verbatim by the Evidence API, including the fields the
   * Facilitator omitted, and the reservation is kept: this is an unresolved payment, not
   * a confirmed non-payment.
   */
  async recordAmbiguousSettlement(args: {
    purchaseId: string;
    mismatchReason: string;
    success: boolean;
    network: string;
    transaction: string;
    payer: string | null;
    amount: string | null;
  }): Promise<AegisPaymentIntentView> {
    return toIntent(
      await this.#post<RawIntent>("/internal/evidence/aegis/payments/ambiguous", {
        purchase_id: args.purchaseId,
        mismatch_reason: args.mismatchReason,
        success: args.success,
        network: args.network,
        transaction: args.transaction,
        payer: args.payer,
        amount: args.amount,
      }),
    );
  }

  async fail(args: { purchaseId: string; reason: string }): Promise<AegisPaymentIntentView> {
    return toIntent(
      await this.#post<RawIntent>("/internal/evidence/aegis/payments/fail", {
        purchase_id: args.purchaseId,
        reason: args.reason,
      }),
    );
  }

  /**
   * Park an attempt whose settlement outcome is unknown.
   *
   * This is not a failure and it never releases the reservation: an ambiguous attempt
   * must never be followed by a fresh payment, so the intent leaves the authorized state
   * only towards reconciliation and stays there until a human resolves it.
   */
  async requireReconciliation(
    purchaseId: string,
    reason: string,
  ): Promise<AegisPaymentIntentView> {
    return toIntent(
      await this.#post<RawIntent>("/internal/evidence/payment-intents/reconciliation", {
        purchase_id: purchaseId,
        reason,
      }),
    );
  }

  async recordDelivery(args: {
    purchaseId: string;
    providerId: string;
    providerModelId: string;
    modelVersion: string;
    responseId: string;
    responseHash: string;
    observedExecutionMs: number;
  }): Promise<void> {
    await this.#post(
      `/internal/evidence/aegis/purchases/${encodeURIComponent(args.purchaseId)}/delivery`,
      {
        provider_id: args.providerId,
        provider_model_id: args.providerModelId,
        model_version: args.modelVersion,
        response_id: args.responseId,
        response_hash: args.responseHash,
        observed_execution_ms: args.observedExecutionMs,
      },
    );
  }
}
