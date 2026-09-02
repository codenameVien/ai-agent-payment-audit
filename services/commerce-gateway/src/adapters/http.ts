import type { Address, Hex } from "viem";

import type {
  EvidenceApi,
  PaymentIntent,
  PaymentView,
  SellerClient,
  SellerResponse,
  StagedDelivery,
} from "../contracts.js";
import type { ReputationEvidenceApi } from "../erc8004.js";
import type {
  EvidenceAnchorApi,
  EvidenceHeadCheckpoint,
} from "../evidence-anchor.js";

async function decodeJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`internal evidence API ${response.status}: ${detail.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

export class EvidenceHttpClient implements EvidenceApi {
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
    return decodeJson<T>(response);
  }

  paymentView(purchaseId: string): Promise<PaymentView> {
    return this.#request(
      `/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/payment-view`,
    );
  }

  claim(purchaseId: string): Promise<PaymentIntent> {
    return this.#post("/internal/evidence/payment-intents/claim", {
      purchase_id: purchaseId,
    });
  }

  authorize(args: {
    purchaseId: string;
    authorizationHash: Hex;
    signature: Hex;
  }): Promise<PaymentIntent> {
    return this.#post("/internal/evidence/payment-intents/authorize", {
      purchase_id: args.purchaseId,
      authorization_hash: args.authorizationHash,
      signature: args.signature,
    });
  }

  reconciliation(
    purchaseId: string,
    reason: string,
    transactionHash?: Hex,
  ): Promise<PaymentIntent> {
    return this.#post("/internal/evidence/payment-intents/reconciliation", {
      purchase_id: purchaseId,
      reason,
      transaction_hash: transactionHash,
    });
  }

  bindReconciliationTransaction(
    purchaseId: string,
    transactionHash: Hex,
  ): Promise<PaymentIntent> {
    return this.#post(
      "/internal/evidence/payment-intents/reconciliation-transaction",
      {
        purchase_id: purchaseId,
        transaction_hash: transactionHash,
      },
    );
  }

  settle(args: {
    purchaseId: string;
    transactionHash: Hex;
    blockNumber: number;
    transferLogIndex: number;
    receiptStatus: number;
    token: string;
    fromAddress: string;
    toAddress: string;
    amountUnits: number;
  }): Promise<PaymentIntent> {
    return this.#post("/internal/evidence/payment-intents/settle", {
      purchase_id: args.purchaseId,
      transaction_hash: args.transactionHash,
      block_number: args.blockNumber,
      transfer_log_index: args.transferLogIndex,
      receipt_status: args.receiptStatus,
      token: args.token,
      from_address: args.fromAddress,
      to_address: args.toAddress,
      amount_units: args.amountUnits,
    });
  }

  failConfirmed(
    purchaseId: string,
    reason: string,
    transactionHash: Hex,
    blockNumber: number,
  ): Promise<PaymentIntent> {
    return this.#post("/internal/evidence/payment-intents/fail", {
      purchase_id: purchaseId,
      reason,
      transaction_hash: transactionHash,
      block_number: blockNumber,
      receipt_status: 0,
    });
  }

  async recordDelivery(args: {
    purchaseId: string;
    sellerAgentId: string;
    providerId: string;
    responseId: string;
    responseHash: string;
    modelId: string;
    modelVersion: string;
  }): Promise<void> {
    await this.#request(
      `/internal/evidence/purchases/${encodeURIComponent(args.purchaseId)}/delivery`,
      {
        method: "POST",
        body: JSON.stringify({
          seller_agent_id: args.sellerAgentId,
          provider_id: args.providerId,
          response_id: args.responseId,
          response_hash: args.responseHash,
          model_id: args.modelId,
          model_version: args.modelVersion,
        }),
      },
    );
  }

  stageDelivery(args: {
    purchaseId: string;
    sellerAgentId: string;
    providerId: string;
    responseId: string;
    modelId: string;
    modelVersion: string;
    text: string;
  }): Promise<StagedDelivery> {
    return this.#request(
      `/internal/evidence/purchases/${encodeURIComponent(args.purchaseId)}/delivery-stage`,
      {
        method: "POST",
        body: JSON.stringify({
          seller_agent_id: args.sellerAgentId,
          provider_id: args.providerId,
          response_id: args.responseId,
          model_id: args.modelId,
          model_version: args.modelVersion,
          text: args.text,
        }),
      },
    );
  }

  async stagedDelivery(purchaseId: string): Promise<StagedDelivery | null> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/delivery-stage`,
      { headers: { authorization: this.#authorization } },
    );
    if (response.status === 404) return null;
    return decodeJson<StagedDelivery>(response);
  }

  #post(path: string, body: unknown): Promise<PaymentIntent> {
    return this.#request(path, { method: "POST", body: JSON.stringify(body) });
  }
}

export class SellerHttpClient implements SellerClient {
  readonly #resourceUrl: string;
  readonly #fetch: typeof fetch;
  readonly #internalAuthorization?: string;

  constructor(args: {
    resourceUrl: string;
    internalServiceToken?: string;
    fetchImpl?: typeof fetch;
  }) {
    this.#resourceUrl = args.resourceUrl;
    this.#fetch = args.fetchImpl ?? fetch;
    this.#internalAuthorization = args.internalServiceToken === undefined
      ? undefined
      : `Bearer ${args.internalServiceToken}`;
  }

  async request(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
    paymentSignature?: string;
    resourceBody?: unknown;
  }): Promise<SellerResponse> {
    const url = new URL(this.#resourceUrl);
    url.searchParams.set("purchaseId", args.purchaseId);
    url.searchParams.set("quoteId", args.quoteId);
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (args.paymentSignature !== undefined) {
      headers["PAYMENT-SIGNATURE"] = args.paymentSignature;
    }
    const response = await this.#fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(args.resourceBody ?? {}),
    });
    const text = await response.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : undefined;
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

  async recover(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
  }): Promise<SellerResponse> {
    const url = new URL(this.#resourceUrl);
    url.pathname = "/internal/executions/recover";
    url.search = "";
    const response = await this.#fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(this.#internalAuthorization === undefined
          ? {}
          : { authorization: this.#internalAuthorization }),
      },
      body: JSON.stringify({ purchaseId: args.purchaseId, quoteId: args.quoteId }),
    });
    const text = await response.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : undefined;
    } catch {
      body = text;
    }
    return {
      status: response.status,
      paymentResponse: response.headers.get("PAYMENT-RESPONSE") ?? undefined,
      body,
    };
  }
}

export class RoutingSellerHttpClient implements SellerClient {
  readonly #routes: ReadonlyMap<string, SellerHttpClient>;

  constructor(
    routes: Record<string, string>,
    fetchImpl?: typeof fetch,
    internalServiceToken?: string,
  ) {
    const entries = Object.entries(routes);
    if (entries.length === 0) throw new Error("at least one seller route is required");
    this.#routes = new Map(
      entries.map(([agentId, resourceUrl]) => [
        agentId,
        new SellerHttpClient({
          resourceUrl,
          ...(fetchImpl === undefined ? {} : { fetchImpl }),
          ...(internalServiceToken === undefined ? {} : { internalServiceToken }),
        }),
      ]),
    );
  }

  request(args: Parameters<SellerClient["request"]>[0]): Promise<SellerResponse> {
    const seller = this.#routes.get(args.sellerAgentId);
    if (seller === undefined) throw new Error(`unknown seller agent ${args.sellerAgentId}`);
    return seller.request(args);
  }

  recover(args: Parameters<SellerClient["recover"]>[0]): Promise<SellerResponse> {
    const seller = this.#routes.get(args.sellerAgentId);
    if (seller === undefined) throw new Error(`unknown seller agent ${args.sellerAgentId}`);
    return seller.recover(args);
  }
}

export class EvidenceAnchorHttpClient implements EvidenceAnchorApi {
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

  async head(purchaseId: string): Promise<EvidenceHeadCheckpoint> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/head`,
      { headers: { authorization: this.#authorization } },
    );
    const body = await decodeJson<{
      purchase_id: string;
      event_count: number;
      head_event_hash: Hex;
    }>(response);
    return {
      purchaseId: body.purchase_id,
      eventCount: body.event_count,
      headEventHash: body.head_event_hash,
    };
  }

  async record(args: {
    checkpoint: EvidenceHeadCheckpoint;
    transactionHash: Hex;
    chainId: number;
    contractAddress: Address;
  }): Promise<void> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(args.checkpoint.purchaseId)}/external-anchors`,
      {
        method: "POST",
        headers: {
          authorization: this.#authorization,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          anchored_event_count: args.checkpoint.eventCount,
          anchored_head_event_hash: args.checkpoint.headEventHash,
          transaction_hash: args.transactionHash,
          chain_id: args.chainId,
          contract_address: args.contractAddress,
        }),
      },
    );
    await decodeJson<unknown>(response);
  }
}

export class ReputationEvidenceHttpClient implements ReputationEvidenceApi {
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

  async prepare(purchaseId: string, agentId: bigint): Promise<{
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    transactionHash?: Hex;
  }> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/reputation-intent?erc8004_agent_id=${agentId}`,
      { headers: { authorization: this.#authorization } },
    );
    const body = await decodeJson<{
      erc8004_agent_id: string;
      objective_value: 0 | 100;
      feedback_hash: Hex;
      transaction_hash?: Hex | null;
    }>(response);
    return {
      agentId: BigInt(body.erc8004_agent_id),
      objectiveValue: body.objective_value,
      feedbackHash: body.feedback_hash,
      ...(body.transaction_hash == null
        ? {}
        : { transactionHash: body.transaction_hash }),
    };
  }

  async record(args: {
    purchaseId: string;
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    transactionHash: Hex;
    chainId: number;
    registryAddress: Address;
  }): Promise<void> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/purchases/${encodeURIComponent(args.purchaseId)}/reputation`,
      {
        method: "POST",
        headers: {
          authorization: this.#authorization,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          erc8004_agent_id: args.agentId.toString(),
          objective_value: args.objectiveValue,
          feedback_hash: args.feedbackHash,
          transaction_hash: args.transactionHash,
          chain_id: args.chainId,
          registry_address: args.registryAddress,
        }),
      },
    );
    await decodeJson<unknown>(response);
  }
}
