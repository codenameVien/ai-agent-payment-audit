import type { Address, Hex } from "viem";

import {
  BASE_SEPOLIA_CHAIN_ID,
  type ConfirmedFeedbackProof,
  type EvidenceApi,
  type PaymentIntent,
  type PaymentView,
  type ReputationOutboxApi,
  type ReputationOutboxStatus,
  type ReputationPublishJob,
  type SellerClient,
  type SellerResponse,
  type StagedDelivery,
  type TransactionRef,
} from "../contracts.js";
import { ReputationOutboxConflictError } from "../erc8004.js";
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

type TransactionRefJson =
  | {
      kind: "EVM";
      hash: Hex;
      chain_id: number;
      evidence_source: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
      block_number?: number | null;
      log_index?: number | null;
    }
  | {
      kind: "LOCAL";
      id: string;
      run_id: string;
      evidence_source: "SYNTHETIC_LOCAL";
    };

interface ReputationJobJson {
  job_id: string;
  status: ReputationOutboxStatus;
  publish_identity: {
    chain_id: number;
    registry_address: Address;
    purchase_id: string;
    seller_agent_id: string;
    tag1: string;
    tag2: string;
  };
  publish_identity_hash: string;
  payload_fingerprint: string;
  decision: {
    decision: "PUBLISH" | "DEFER";
    value: number | null;
    reason_codes: string[];
    audit_bundle_hash: string;
    ruleset_version: string;
    seller_agent_id: string;
    erc8004_agent_id: string;
  };
  attempt_count: number;
  worker_id: string | null;
  lease_expires_at: string | null;
  feedback_hash: Hex | null;
  transaction_ref: TransactionRefJson | null;
  receipt_proof_ref: string | null;
  created_at: string;
  updated_at: string;
}

function encodeTransactionRef(reference: TransactionRef): TransactionRefJson {
  if (reference.kind === "LOCAL") {
    return {
      kind: "LOCAL",
      id: reference.id,
      run_id: reference.runId,
      evidence_source: "SYNTHETIC_LOCAL",
    };
  }
  return {
    kind: "EVM",
    hash: reference.hash,
    chain_id: reference.chainId,
    evidence_source: reference.evidenceSource,
    ...(reference.blockNumber === undefined ? {} : { block_number: reference.blockNumber }),
    ...(reference.logIndex === undefined ? {} : { log_index: reference.logIndex }),
  };
}

function decodeTransactionRef(body: TransactionRefJson): TransactionRef {
  if (body.kind === "LOCAL") {
    return {
      kind: "LOCAL",
      id: body.id,
      runId: body.run_id,
      evidenceSource: "SYNTHETIC_LOCAL",
    };
  }
  if (body.chain_id !== BASE_SEPOLIA_CHAIN_ID) {
    throw new Error(`reputation outbox returned an off-chain-id transaction: ${body.chain_id}`);
  }
  return {
    kind: "EVM",
    hash: body.hash,
    chainId: BASE_SEPOLIA_CHAIN_ID,
    evidenceSource: body.evidence_source,
    ...(body.block_number == null ? {} : { blockNumber: body.block_number }),
    ...(body.log_index == null ? {} : { logIndex: body.log_index }),
  };
}

function decodeJob(body: ReputationJobJson): ReputationPublishJob {
  return {
    jobId: body.job_id,
    status: body.status,
    identity: {
      chainId: body.publish_identity.chain_id,
      registryAddress: body.publish_identity.registry_address,
      purchaseId: body.publish_identity.purchase_id,
      sellerAgentId: body.publish_identity.seller_agent_id,
      tag1: body.publish_identity.tag1,
      tag2: body.publish_identity.tag2,
    },
    identityHash: body.publish_identity_hash,
    payloadFingerprint: body.payload_fingerprint,
    decision: {
      decision: body.decision.decision,
      value: body.decision.value,
      reasonCodes: body.decision.reason_codes,
      auditBundleHash: body.decision.audit_bundle_hash,
      rulesetVersion: body.decision.ruleset_version,
      sellerAgentId: body.decision.seller_agent_id,
      erc8004AgentId: body.decision.erc8004_agent_id,
    },
    attemptCount: body.attempt_count,
    workerId: body.worker_id,
    leaseExpiresAt: body.lease_expires_at,
    feedbackHash: body.feedback_hash,
    transactionRef: body.transaction_ref === null
      ? null
      : decodeTransactionRef(body.transaction_ref),
    receiptProofRef: body.receipt_proof_ref,
    createdAt: body.created_at,
    updatedAt: body.updated_at,
  };
}

/**
 * Durable reputation state over HTTP. Every lease, transition and recorded transaction
 * belongs to the Evidence API, so exactly-once survives a process restart.
 */
export class ReputationOutboxHttpClient implements ReputationOutboxApi {
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

  async claim(args: { workerId: string; leaseSeconds: number }): Promise<ReputationPublishJob | null> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/reputation-outbox/claim`,
      {
        method: "POST",
        headers: {
          authorization: this.#authorization,
          "content-type": "application/json",
        },
        body: JSON.stringify({ worker_id: args.workerId, lease_seconds: args.leaseSeconds }),
      },
    );
    if (response.status === 404) return null;
    return decodeJob(await this.#decode(response));
  }

  async get(jobId: string): Promise<ReputationPublishJob | null> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/reputation-outbox/${encodeURIComponent(jobId)}`,
      { headers: { authorization: this.#authorization } },
    );
    if (response.status === 404) return null;
    return decodeJob(await this.#decode(response));
  }

  async markPrepared(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    transactionRef?: TransactionRef;
    feedbackHash: Hex;
  }): Promise<ReputationPublishJob> {
    return this.#transition(args.jobId, "prepared", {
      worker_id: args.workerId,
      payload_fingerprint: args.payloadFingerprint,
      feedback_hash: args.feedbackHash,
      ...(args.transactionRef === undefined
        ? {}
        : { transaction_ref: encodeTransactionRef(args.transactionRef) }),
    });
  }

  async markSubmittedUnknown(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    transactionRef: TransactionRef;
    reason: string;
  }): Promise<ReputationPublishJob> {
    return this.#transition(args.jobId, "submitted-unknown", {
      worker_id: args.workerId,
      payload_fingerprint: args.payloadFingerprint,
      transaction_ref: encodeTransactionRef(args.transactionRef),
      reason: args.reason,
    });
  }

  async markConfirmed(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    proof: ConfirmedFeedbackProof;
  }): Promise<ReputationPublishJob> {
    return this.#transition(args.jobId, "confirmed", {
      worker_id: args.workerId,
      payload_fingerprint: args.payloadFingerprint,
      proof: {
        transaction_ref: encodeTransactionRef(args.proof.transactionRef),
        receipt_proof_ref: args.proof.receiptProofRef,
        client_address: args.proof.clientAddress.toLowerCase(),
        erc8004_agent_id: args.proof.erc8004AgentId,
        value: args.proof.value,
        value_decimals: args.proof.valueDecimals,
        feedback_hash: args.proof.feedbackHash,
        block_number: args.proof.blockNumber,
        log_index: args.proof.logIndex,
        tag1: args.proof.tag1,
        tag2: args.proof.tag2,
        feedback_uri: args.proof.feedbackUri,
      },
    });
  }

  async #transition(
    jobId: string,
    action: "prepared" | "submitted-unknown" | "confirmed",
    body: unknown,
  ): Promise<ReputationPublishJob> {
    const response = await this.#fetch(
      `${this.#baseUrl}/internal/evidence/reputation-outbox/${encodeURIComponent(jobId)}/${action}`,
      {
        method: "POST",
        headers: {
          authorization: this.#authorization,
          "content-type": "application/json",
        },
        body: JSON.stringify(body),
      },
    );
    return decodeJob(await this.#decode(response));
  }

  /** A 409 is a durable-state refusal, never a transport failure worth retrying. */
  async #decode(response: Response): Promise<ReputationJobJson> {
    if (response.status === 409) {
      const detail = await response.text();
      throw new ReputationOutboxConflictError(detail.slice(0, 300));
    }
    return decodeJson<ReputationJobJson>(response);
  }
}
