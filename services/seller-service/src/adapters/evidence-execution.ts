import type {
  PaymentAuthorization,
  PaymentSettlement,
  ProviderResult,
  SellerExecutionRecord,
  SellerExecutionStore,
  SellerExecutionState,
} from "../contracts.js";

interface ExecutionWire {
  purchase_id: string;
  quote_id: string;
  seller_agent_id: string;
  prompt: string;
  prompt_hash: string;
  payment_proof_hash: string;
  state: SellerExecutionState;
  authorization?: PaymentAuthorization | null;
  settlement?: PaymentSettlement | null;
  provider_attempt_id?: string | null;
  provider_attempt_token?: string | null;
  result?: ProviderResult | null;
}

function fromWire(value: ExecutionWire): SellerExecutionRecord {
  return {
    purchaseId: value.purchase_id,
    quoteId: value.quote_id,
    sellerAgentId: value.seller_agent_id,
    prompt: value.prompt,
    promptHash: value.prompt_hash,
    paymentProofHash: value.payment_proof_hash,
    state: value.state,
    ...(value.authorization == null ? {} : { authorization: value.authorization }),
    ...(value.settlement == null ? {} : { settlement: value.settlement }),
    ...(value.provider_attempt_id == null
      ? {}
      : { providerAttemptId: value.provider_attempt_id }),
    ...(value.provider_attempt_token == null
      ? {}
      : { providerAttemptToken: value.provider_attempt_token }),
    ...(value.result == null ? {} : { result: value.result }),
  };
}

export class EvidenceSellerExecutionStore implements SellerExecutionStore {
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

  async #request(path: string, init?: RequestInit): Promise<SellerExecutionRecord> {
    const response = await this.#fetch(`${this.#baseUrl}${path}`, {
      ...init,
      headers: {
        authorization: this.#authorization,
        "content-type": "application/json",
        ...init?.headers,
      },
    });
    if (!response.ok) throw new Error(`seller execution API failed with ${response.status}`);
    return fromWire(await response.json() as ExecutionWire);
  }

  claim(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
    prompt: string;
    promptHash: string;
    paymentProofHash: string;
  }): Promise<SellerExecutionRecord> {
    return this.#request("/internal/evidence/seller-executions/claim", {
      method: "POST",
      body: JSON.stringify({
        purchase_id: args.purchaseId,
        quote_id: args.quoteId,
        seller_agent_id: args.sellerAgentId,
        prompt: args.prompt,
        prompt_hash: args.promptHash,
        payment_proof_hash: args.paymentProofHash,
      }),
    });
  }

  async get(purchaseId: string): Promise<SellerExecutionRecord | null> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/seller-executions/${encodeURIComponent(purchaseId)}`,
      { headers: { authorization: this.#authorization } },
    );
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`seller execution API failed with ${response.status}`);
    return fromWire(await response.json() as ExecutionWire);
  }

  recordAuthorization(
    purchaseId: string,
    authorization: PaymentAuthorization,
  ): Promise<SellerExecutionRecord> {
    return this.#record("authorization", purchaseId, authorization);
  }

  recordSettlement(
    purchaseId: string,
    settlement: PaymentSettlement,
  ): Promise<SellerExecutionRecord> {
    return this.#record("settlement", purchaseId, settlement);
  }

  recordProviderSubmission(
    purchaseId: string,
    providerAttemptId: string,
    providerAttemptToken: string,
  ): Promise<SellerExecutionRecord> {
    return this.#request("/internal/evidence/seller-executions/provider-submission", {
      method: "POST",
      body: JSON.stringify({
        purchase_id: purchaseId,
        provider_attempt_id: providerAttemptId,
        provider_attempt_token: providerAttemptToken,
      }),
    });
  }

  recordResult(
    purchaseId: string,
    result: ProviderResult,
  ): Promise<SellerExecutionRecord> {
    return this.#record("result", purchaseId, result);
  }

  #record(
    kind: "authorization" | "settlement" | "result",
    purchaseId: string,
    value: unknown,
  ): Promise<SellerExecutionRecord> {
    return this.#request(`/internal/evidence/seller-executions/${kind}`, {
      method: "POST",
      body: JSON.stringify({ purchase_id: purchaseId, value }),
    });
  }
}
