import { createHash } from "node:crypto";

import type { Address, Hex } from "viem";

import {
  BASE_SEPOLIA_CHAIN_ID,
  TERMINAL_PAYMENT_INTENT_STATE,
  type Clock,
  type ConfirmedMismatchProof,
  type DecisionSigner,
  type Erc3009Signer,
  type EvidenceApi,
  type EvmReceiptProof,
  type EvmTransactionRef,
  type LocalReceiptProof,
  type LocalTransactionRef,
  type NoTransferProof,
  type PaymentIntent,
  type PaymentExecutionResult,
  type PaymentView,
  type QuoteIdentityVerifier,
  type ReceiptAuthorization,
  type ReceiptProof,
  type ReceiptReader,
  type ReceiptTransfer,
  type ReconciliationCheck,
  type ReconciliationVerifierOutcome,
  type SellerClient,
  type StagedDelivery,
  type TerminalPaymentEvidenceApi,
  type TransactionRef,
} from "./contracts.js";
import { digestToBytes32 } from "./digest.js";
import {
  BASE_SEPOLIA_NETWORK,
  PBLC_TOKEN_NAME,
  PBLC_V2_TOKEN_VERSION,
  createErc3009Authorization,
  createErc3009PaymentPayload,
  decodePaymentRequired,
  decodeSettlementResponse,
  encodeHeader,
  selectBoundRequirement,
  X402BindingError,
} from "./x402.js";

const EVM_TRANSACTION_HASH_PATTERN = /^0x[0-9a-f]{64}$/;
const LOCAL_TRANSACTION_ID_PATTERN =
  /^localtx:[0-9a-f]{32}:(payment|feedback):[0-9]{6}$/;
const SCENARIO_RUN_ID_PATTERN = /^[0-9a-f]{32}$/;
const DEFAULT_BOUNDED_RECONCILIATION_ATTEMPTS = 3;
const DEFAULT_MINIMUM_FINALITY_CONFIRMATIONS = 2;

export class CommerceGatewayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CommerceGatewayError";
  }
}

export class TransactionRefError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TransactionRefError";
  }
}

const EVM_EVIDENCE_SOURCE: Record<string, "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN"> = {
  BASE_SEPOLIA_VERIFIED: "BASE_SEPOLIA_VERIFIED",
  HISTORICAL_ON_CHAIN: "HISTORICAL_ON_CHAIN",
};

function assertCoordinate(value: number | undefined, field: string): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isInteger(value) || value < 0) {
    throw new TransactionRefError(`${field} must be a non-negative integer`);
  }
  return value;
}

/** Builds the EVM variant, rejecting local identifiers, unknown sources and bad coordinates. */
export function evmTransactionRef(args: {
  hash: string;
  evidenceSource?: string;
  chainId?: number;
  blockNumber?: number;
  logIndex?: number;
}): EvmTransactionRef {
  if (args.hash.startsWith("localtx:")) {
    throw new TransactionRefError("a local transaction id cannot be a transactionHash");
  }
  if (!EVM_TRANSACTION_HASH_PATTERN.test(args.hash)) {
    throw new TransactionRefError("EVM transaction hash must match ^0x[0-9a-f]{64}$");
  }
  if ((args.chainId ?? BASE_SEPOLIA_CHAIN_ID) !== BASE_SEPOLIA_CHAIN_ID) {
    throw new TransactionRefError("EVM transaction reference must be on Base Sepolia");
  }
  if (args.evidenceSource === "SYNTHETIC_LOCAL") {
    throw new TransactionRefError("synthetic evidence cannot use an EVM transaction reference");
  }
  const source = EVM_EVIDENCE_SOURCE[args.evidenceSource ?? "BASE_SEPOLIA_VERIFIED"];
  if (source === undefined) {
    throw new TransactionRefError(
      `unknown EVM evidence source: ${String(args.evidenceSource)}`,
    );
  }
  const blockNumber = assertCoordinate(args.blockNumber, "blockNumber");
  const logIndex = assertCoordinate(args.logIndex, "logIndex");
  return {
    kind: "EVM",
    hash: args.hash as Hex,
    chainId: BASE_SEPOLIA_CHAIN_ID,
    evidenceSource: source,
    ...(blockNumber === undefined ? {} : { blockNumber }),
    ...(logIndex === undefined ? {} : { logIndex }),
  };
}

/** Builds the local variant, rejecting EVM hashes and foreign run identifiers. */
export function localTransactionRef(args: {
  id: string;
  runId: string;
}): LocalTransactionRef {
  if (args.id.startsWith("0x")) {
    throw new TransactionRefError("an EVM transaction hash cannot be a localTransactionId");
  }
  if (!LOCAL_TRANSACTION_ID_PATTERN.test(args.id)) {
    throw new TransactionRefError(
      "local transaction id must match ^localtx:[0-9a-f]{32}:(payment|feedback):[0-9]{6}$",
    );
  }
  if (!SCENARIO_RUN_ID_PATTERN.test(args.runId) || !args.id.startsWith(`localtx:${args.runId}:`)) {
    throw new TransactionRefError("local transaction id does not belong to this runId");
  }
  return { kind: "LOCAL", id: args.id, runId: args.runId, evidenceSource: "SYNTHETIC_LOCAL" };
}

/** Parses the persisted union without letting either variant impersonate the other. */
export function assertTransactionRef(value: unknown): TransactionRef {
  if (typeof value !== "object" || value === null) {
    throw new TransactionRefError("transaction reference is malformed");
  }
  const candidate = value as Record<string, unknown>;
  if ("hash" in candidate && ("id" in candidate || "localTransactionId" in candidate)) {
    throw new TransactionRefError(
      "a payload cannot carry both transactionHash and localTransactionId",
    );
  }
  if (candidate.kind === "EVM") {
    return evmTransactionRef({
      hash: String(candidate.hash ?? ""),
      evidenceSource:
        candidate.evidenceSource === undefined
          ? undefined
          : String(candidate.evidenceSource),
      chainId: typeof candidate.chainId === "number" ? candidate.chainId : undefined,
      blockNumber: typeof candidate.blockNumber === "number" ? candidate.blockNumber : undefined,
      logIndex: typeof candidate.logIndex === "number" ? candidate.logIndex : undefined,
    });
  }
  if (candidate.kind === "LOCAL") {
    if (
      candidate.evidenceSource !== undefined &&
      candidate.evidenceSource !== "SYNTHETIC_LOCAL"
    ) {
      throw new TransactionRefError("local transaction references are always SYNTHETIC_LOCAL");
    }
    return localTransactionRef({
      id: String(candidate.id ?? ""),
      runId: String(candidate.runId ?? ""),
    });
  }
  throw new TransactionRefError("transaction reference kind must be EVM or LOCAL");
}

/** Runtime capability check for the terminal Evidence API operations. */
export function isTerminalEvidenceApi(value: unknown): value is TerminalPaymentEvidenceApi {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.recordReconciliationCheck === "function" &&
    typeof candidate.confirmMismatch === "function" &&
    typeof candidate.reconcileNoTransfer === "function"
  );
}

function sha256Ref(value: string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function evidenceHashToBytes32(value: string): Hex {
  try {
    return digestToBytes32(value);
  } catch {
    throw new CommerceGatewayError("decision evidence hash is not a bytes32 SHA-256 digest");
  }
}

/** The submission identity a payment intent is currently bound to, if any. */
export function intentSubmissionRef(intent: PaymentIntent): TransactionRef | null {
  const hash = intent.transaction_hash ?? null;
  const local = intent.local_transaction_id ?? null;
  if (hash !== null && local !== null) {
    throw new TransactionRefError(
      "a payment intent cannot carry both transactionHash and localTransactionId",
    );
  }
  if (hash !== null) return evmTransactionRef({ hash: hash.toLowerCase() });
  if (local !== null) {
    return localTransactionRef({ id: local, runId: local.split(":")[1] ?? "" });
  }
  return null;
}

/**
 * A receipt narrowed to exactly one variant, with every coordinate and the declared
 * evidence source validated against the submission the gateway actually asked about.
 */
export type ClassifiedReceipt =
  | { kind: "EVM"; proof: EvmReceiptProof; reference: EvmTransactionRef; confirmations: number }
  | { kind: "LOCAL"; proof: LocalReceiptProof; reference: LocalTransactionRef; confirmations: number };

export function classifyReceipt(
  receipt: ReceiptProof,
  submission: TransactionRef,
): ClassifiedReceipt {
  const hasHash = receipt.transactionHash !== undefined;
  const hasLocal = receipt.localTransactionId !== undefined;
  if (hasHash === hasLocal) {
    throw new TransactionRefError(
      "a receipt must carry exactly one of transactionHash or localTransactionId",
    );
  }
  const confirmations = assertCoordinate(receipt.confirmations, "confirmations") ?? 0;
  for (const transfer of receipt.transfers) {
    assertCoordinate(transfer.logIndex, "transfer logIndex");
  }
  for (const authorization of receipt.authorizations ?? []) {
    assertCoordinate(authorization.logIndex, "authorization logIndex");
  }
  if (hasHash) {
    const proof = receipt as EvmReceiptProof;
    if (submission.kind !== "EVM") {
      throw new TransactionRefError("an EVM receipt cannot prove a local submission");
    }
    const reference = evmTransactionRef({
      hash: proof.transactionHash.toLowerCase(),
      evidenceSource: proof.evidenceSource,
      blockNumber: proof.blockNumber,
    });
    if (reference.hash !== submission.hash) {
      throw new TransactionRefError("receipt transaction hash is not the submitted transaction");
    }
    // H1: the same hash under a different provenance is a different claim. An active
    // EIP-3009 submission is BASE_SEPOLIA_VERIFIED, so HISTORICAL_ON_CHAIN can never prove it.
    if (reference.evidenceSource !== submission.evidenceSource) {
      throw new TransactionRefError(
        "receipt evidence source is not the submitted evidence source",
      );
    }
    return { kind: "EVM", proof, reference, confirmations };
  }
  const proof = receipt as LocalReceiptProof;
  if (submission.kind !== "LOCAL") {
    throw new TransactionRefError("a local receipt cannot prove an EVM submission");
  }
  if (proof.evidenceSource !== undefined && proof.evidenceSource !== "SYNTHETIC_LOCAL") {
    throw new TransactionRefError("a local receipt is always SYNTHETIC_LOCAL");
  }
  const reference = localTransactionRef({ id: proof.localTransactionId, runId: proof.runId });
  if (reference.id !== submission.id || reference.runId !== submission.runId) {
    throw new TransactionRefError("local receipt identity is not the submitted identity");
  }
  if (reference.evidenceSource !== submission.evidenceSource) {
    throw new TransactionRefError(
      "receipt evidence source is not the submitted evidence source",
    );
  }
  return { kind: "LOCAL", proof, reference, confirmations };
}

export class CommerceGateway {
  readonly #evidence: EvidenceApi;
  readonly #seller: SellerClient;
  readonly #decisionSigner: DecisionSigner;
  readonly #erc3009Signer: Erc3009Signer;
  readonly #receipts: ReceiptReader;
  readonly #identityVerifier: QuoteIdentityVerifier;
  readonly #clock: Clock;
  readonly #terminal: TerminalPaymentEvidenceApi | null;
  readonly #boundedReconciliationAttempts: number;
  readonly #minimumFinalityConfirmations: number;

  constructor(args: {
    evidence: EvidenceApi;
    seller: SellerClient;
    decisionSigner: DecisionSigner;
    erc3009Signer: Erc3009Signer;
    receipts: ReceiptReader;
    identityVerifier: QuoteIdentityVerifier;
    clock: Clock;
    terminalEvidence?: TerminalPaymentEvidenceApi;
    boundedReconciliationAttempts?: number;
    minimumFinalityConfirmations?: number;
  }) {
    this.#evidence = args.evidence;
    this.#seller = args.seller;
    this.#decisionSigner = args.decisionSigner;
    this.#erc3009Signer = args.erc3009Signer;
    this.#receipts = args.receipts;
    this.#identityVerifier = args.identityVerifier;
    this.#clock = args.clock;
    const terminal = args.terminalEvidence ?? args.evidence;
    this.#terminal = isTerminalEvidenceApi(terminal) ? terminal : null;
    this.#boundedReconciliationAttempts =
      args.boundedReconciliationAttempts ?? DEFAULT_BOUNDED_RECONCILIATION_ATTEMPTS;
    this.#minimumFinalityConfirmations =
      args.minimumFinalityConfirmations ?? DEFAULT_MINIMUM_FINALITY_CONFIRMATIONS;
  }

  async execute(purchaseId: string, resourceBody?: unknown): Promise<PaymentExecutionResult> {
    const view = await this.#evidence.paymentView(purchaseId);
    await this.#identityVerifier.verifyQuoteSigner(
      BigInt(view.quote.erc8004_agent_id),
      view.quote.signer_address,
    );
    const intent = await this.#evidence.claim(purchaseId);
    this.#assertViewIntent(view, intent);
    // C1: every terminal state must stop here, before any seller, signing or submission call.
    if (intent.state === "SETTLED") {
      const staged = await this.#evidence.stagedDelivery(purchaseId);
      return staged === null ? intent : this.#finishStaged(purchaseId, intent, staged);
    }
    if (TERMINAL_PAYMENT_INTENT_STATE[intent.state]) {
      return intent;
    }
    if (intent.transfer_method !== "eip3009") {
      throw new CommerceGatewayError(
        "legacy Permit2 payment intents are read-only and cannot be executed",
      );
    }
    const boundSubmission = intentSubmissionRef(intent);
    if (intent.state === "RECONCILIATION_REQUIRED" && boundSubmission !== null) {
      const outcome = await this.#reconcileTransaction(purchaseId, intent, boundSubmission);
      const staged = await this.#evidence.stagedDelivery(purchaseId);
      return staged === null ? outcome : this.#finishStaged(purchaseId, outcome, staged);
    }
    if (intent.state === "RECONCILIATION_REQUIRED") {
      const recovered = await this.#seller.recover({
        purchaseId,
        quoteId: intent.quote_id,
        sellerAgentId: view.quote.seller_agent_id,
      });
      if (!recovered.paymentResponse) return intent;
      return this.#consumePaidResponse(purchaseId, view.quote, recovered, true);
    }

    const challenge = await this.#seller.request({
      purchaseId,
      quoteId: intent.quote_id,
      sellerAgentId: view.quote.seller_agent_id,
      resourceBody,
    });
    if (challenge.status !== 402 || !challenge.paymentRequired) {
      throw new CommerceGatewayError("seller did not return a canonical 402 challenge");
    }
    const required = decodePaymentRequired(challenge.paymentRequired);
    const requirement = selectBoundRequirement(required, intent);

    const commonDecision = {
      purchaseId,
      decisionEventHash: evidenceHashToBytes32(intent.decision_event_hash),
      quoteId: intent.quote_id,
      amount: BigInt(intent.amount_units),
      token: intent.token,
      payTo: intent.pay_to,
    };
    if (intent.authorization_nonce === undefined || intent.authorization_nonce === null) {
      throw new CommerceGatewayError("ERC-3009 authorization nonce is unavailable");
    }
    const signedDecision = await this.#decisionSigner.signErc3009({
      ...commonDecision,
      authorizationNonce: intent.authorization_nonce,
    });
    if (intent.state === "CLAIMED") {
      await this.#evidence.authorize({
        purchaseId,
        authorizationHash: signedDecision.hash,
        signature: signedDecision.signature,
      });
    } else if (
      intent.decision_authorization_hash !== signedDecision.hash ||
      intent.decision_authorization_signature !== signedDecision.signature
    ) {
      throw new CommerceGatewayError("resumed payment authorization changed");
    }

    const now = this.#clock.nowSeconds();
    const quoteDeadline = BigInt(Math.floor(Date.parse(view.quote.expires_at) / 1000));
    const timeoutDeadline = now + BigInt(requirement.maxTimeoutSeconds);
    const deadline = quoteDeadline < timeoutDeadline ? quoteDeadline : timeoutDeadline;
    const authorization = createErc3009Authorization({
      intent,
      validAfter: 0n,
      validBefore: deadline,
    });
    const signature = await this.#erc3009Signer.sign({
      token: intent.token,
      tokenName: String(requirement.extra?.name ?? PBLC_TOKEN_NAME),
      tokenVersion: String(requirement.extra?.version ?? PBLC_V2_TOKEN_VERSION),
      authorization,
    });
    const payload = createErc3009PaymentPayload({
      resource: required.resource,
      requirement,
      authorization,
      signature,
    });
    const paid = await this.#seller.request({
      purchaseId,
      quoteId: intent.quote_id,
      sellerAgentId: view.quote.seller_agent_id,
      paymentSignature: encodeHeader(payload),
      resourceBody,
    });
    return this.#consumePaidResponse(purchaseId, view.quote, paid);
  }

  async #consumePaidResponse(
    purchaseId: string,
    quote: PaymentView["quote"],
    paid: Awaited<ReturnType<SellerClient["request"]>>,
    recoveredFromHashless = false,
  ): Promise<PaymentExecutionResult> {
    if (!paid.paymentResponse) {
      const sellerDetail =
        typeof paid.body === "object" && paid.body !== null
        && typeof (paid.body as Record<string, unknown>).error === "string"
          ? String((paid.body as Record<string, unknown>).error).slice(0, 240)
          : undefined;
      return this.#evidence.reconciliation(
        purchaseId,
        sellerDetail === undefined
          ? `seller response ${paid.status} omitted PAYMENT-RESPONSE after authorization`
          : `seller response ${paid.status}: ${sellerDetail}`,
      );
    }
    let settlement;
    try {
      settlement = decodeSettlementResponse(paid.paymentResponse);
    } catch (error) {
      if (error instanceof X402BindingError) {
        return this.#evidence.reconciliation(purchaseId, error.message);
      }
      throw error;
    }
    if (settlement.network !== BASE_SEPOLIA_NETWORK) {
      return this.#evidence.reconciliation(
        purchaseId,
        "facilitator response network mismatch",
      );
    }
    if (!settlement.transaction || !/^0x[0-9a-fA-F]{64}$/.test(settlement.transaction)) {
      return this.#evidence.reconciliation(
        purchaseId,
        settlement.errorReason ?? "facilitator returned no transaction hash",
      );
    }
    const transactionHash = settlement.transaction as Hex;
    if (!settlement.success) {
      const submitted = await this.#recordSubmittedTransaction(
        purchaseId,
        settlement.errorReason ?? "facilitator returned submitted transaction",
        transactionHash,
        recoveredFromHashless,
      );
      return this.#reconcileTransaction(
        purchaseId,
        submitted,
        evmTransactionRef({ hash: transactionHash.toLowerCase() }),
      );
    }
    let staged: StagedDelivery;
    try {
      staged = await this.#stageProviderResult(purchaseId, quote, paid.body);
    } catch (error) {
      const reason = error instanceof Error ? error.message : "seller delivery unavailable";
      const submitted = await this.#recordSubmittedTransaction(
        purchaseId,
        reason,
        transactionHash,
        recoveredFromHashless,
      );
      return this.#reconcileTransaction(
        purchaseId,
        submitted,
        evmTransactionRef({ hash: transactionHash.toLowerCase() }),
      );
    }
    const submitted = await this.#recordSubmittedTransaction(
      purchaseId,
      "facilitator returned submitted transaction",
      transactionHash,
      recoveredFromHashless,
    );
    return this.#finishStaged(
      purchaseId,
      await this.#reconcileTransaction(
        purchaseId,
        submitted,
        evmTransactionRef({ hash: transactionHash.toLowerCase() }),
      ),
      staged,
    );
  }

  #recordSubmittedTransaction(
    purchaseId: string,
    reason: string,
    transactionHash: Hex,
    recoveredFromHashless: boolean,
  ): Promise<PaymentIntent> {
    return recoveredFromHashless
      ? this.#evidence.bindReconciliationTransaction(purchaseId, transactionHash)
      : this.#evidence.reconciliation(purchaseId, reason, transactionHash);
  }

  async #stageProviderResult(
    purchaseId: string,
    quote: {
      seller_agent_id: string;
      provider_id: string;
      model_id: string;
      model_version: string;
    },
    body: unknown,
  ): Promise<StagedDelivery> {
    if (typeof body !== "object" || body === null) {
      throw new CommerceGatewayError("settled seller response is malformed");
    }
    const result = body as Record<string, unknown>;
    for (const field of ["responseId", "modelId", "modelVersion", "text"] as const) {
      if (typeof result[field] !== "string" || result[field].length === 0) {
        throw new CommerceGatewayError(`settled seller response missing ${field}`);
      }
    }
    if (typeof result.providerId !== "string" || result.providerId.length === 0) {
      throw new CommerceGatewayError("settled seller response missing providerId");
    }
    if (
      result.providerId !== quote.provider_id ||
      result.modelId !== quote.model_id ||
      result.modelVersion !== quote.model_version
    ) {
      throw new CommerceGatewayError("seller delivery changed selected model binding");
    }
    return this.#evidence.stageDelivery({
      purchaseId,
      sellerAgentId: quote.seller_agent_id,
      providerId: result.providerId,
      responseId: result.responseId as string,
      modelId: result.modelId as string,
      modelVersion: result.modelVersion as string,
      text: result.text as string,
    });
  }

  async #finishStaged(
    purchaseId: string,
    intent: PaymentIntent,
    staged: StagedDelivery,
  ): Promise<PaymentExecutionResult> {
    if (intent.state !== "SETTLED") return intent;
    await this.#evidence.recordDelivery({
      purchaseId,
      sellerAgentId: staged.seller_agent_id,
      providerId: staged.provider_id,
      responseId: staged.response_id,
      responseHash: staged.response_hash,
      modelId: staged.model_id,
      modelVersion: staged.model_version,
    });
    return {
      ...intent,
      provider_result: {
        providerId: staged.provider_id,
        responseId: staged.response_id,
        modelId: staged.model_id,
        modelVersion: staged.model_version,
        text: staged.text,
      },
    };
  }

  async reconcile(purchaseId: string, transactionHash: Hex): Promise<PaymentIntent> {
    const intent = await this.#evidence.claim(purchaseId);
    if (intent.transfer_method !== "eip3009") {
      throw new CommerceGatewayError(
        "legacy Permit2 payment intents are read-only and cannot be reconciled",
      );
    }
    // C1: a terminal payment is never re-examined or re-submitted.
    if (TERMINAL_PAYMENT_INTENT_STATE[intent.state]) {
      return intent;
    }
    const submission = intentSubmissionRef(intent);
    if (
      intent.state !== "RECONCILIATION_REQUIRED" ||
      submission === null ||
      submission.kind !== "EVM" ||
      submission.hash !== transactionHash.toLowerCase()
    ) {
      throw new CommerceGatewayError("transaction hash is not bound to this payment intent");
    }
    return this.#reconcileTransaction(purchaseId, intent, submission);
  }

  /**
   * Records one bounded reconciliation attempt. Without the terminal Evidence API
   * capability the intent is returned unchanged, so no attempt evidence is invented.
   */
  async #recordCheck(
    purchaseId: string,
    intent: PaymentIntent,
    args: {
      reference: TransactionRef;
      outcome: ReconciliationVerifierOutcome;
      confirmations: number;
      receiptStatus?: 0 | 1;
      blockNumber?: number;
      authorizationState?: string;
    },
  ): Promise<PaymentIntent> {
    if (this.#terminal === null) return intent;
    const attemptNumber = (intent.reconciliation_attempt_count ?? 0) + 1;
    const submissionRef =
      args.reference.kind === "LOCAL" ? args.reference.id : args.reference.hash;
    const check: ReconciliationCheck = {
      purchaseId,
      attemptNumber,
      checkedAt: new Date(Number(this.#clock.nowSeconds()) * 1000).toISOString(),
      checkedChainId: BASE_SEPOLIA_CHAIN_ID,
      submissionRef,
      verifierOutcome: args.outcome,
      finalityConfirmations: args.confirmations,
      proofRef: sha256Ref(`${submissionRef}:${attemptNumber}:${args.outcome}`),
      evidenceSource:
        args.reference.kind === "LOCAL" ? "SYNTHETIC_LOCAL" : args.reference.evidenceSource,
      ...(args.receiptStatus === undefined ? {} : { receiptStatus: args.receiptStatus }),
      ...(args.blockNumber === undefined ? {} : { blockNumber: args.blockNumber }),
      ...(args.authorizationState === undefined
        ? {}
        : { authorizationState: args.authorizationState }),
    };
    return this.#terminal.recordReconciliationCheck(check);
  }

  async #reconcileTransaction(
    purchaseId: string,
    intent: PaymentIntent,
    submission: TransactionRef,
  ): Promise<PaymentIntent> {
    const submissionRef = submission.kind === "LOCAL" ? submission.id : submission.hash;
    const evmHash = submission.kind === "EVM" ? submission.hash : undefined;
    const raw = await this.#receipts.read(submissionRef);
    if (raw === null) {
      await this.#recordCheck(purchaseId, intent, {
        reference: submission,
        outcome: "RECEIPT_NOT_FOUND",
        confirmations: 0,
      });
      return this.#evidence.reconciliation(
        purchaseId,
        `receipt pending for ${submissionRef}`,
        evmHash,
      );
    }
    let receipt: ClassifiedReceipt;
    try {
      receipt = classifyReceipt(raw, submission);
    } catch (error) {
      // A proof that does not match the submission it answers is never usable evidence.
      await this.#recordCheck(purchaseId, intent, {
        reference: submission,
        outcome: "RECEIPT_HASH_MISMATCH",
        confirmations: 0,
      });
      return this.#evidence.reconciliation(
        purchaseId,
        error instanceof TransactionRefError
          ? error.message
          : "receipt proof does not match the submitted payment",
        evmHash,
      );
    }
    const { proof, reference, confirmations } = receipt;
    if (proof.status === 0) {
      await this.#recordCheck(purchaseId, intent, {
        reference,
        outcome: "RECEIPT_REVERTED",
        confirmations,
        receiptStatus: 0,
        ...(proof.blockNumber === undefined ? {} : { blockNumber: proof.blockNumber }),
      });
      if (receipt.kind !== "EVM") {
        return this.#evidence.reconciliation(
          purchaseId,
          "synthetic reverted proof cannot fail an on-chain payment intent",
          evmHash,
        );
      }
      return this.#evidence.failConfirmed(
        purchaseId,
        "independent receipt status zero",
        receipt.reference.hash,
        receipt.proof.blockNumber,
      );
    }
    const buyerOutflows = proof.transfers.filter(
      (transfer) =>
        transfer.from.toLowerCase() === intent.buyer_wallet_address.toLowerCase(),
    );
    const matches = buyerOutflows.filter(
      (transfer) =>
        transfer.token.toLowerCase() === intent.token.toLowerCase() &&
        transfer.to.toLowerCase() === intent.pay_to.toLowerCase() &&
        transfer.amount === BigInt(intent.amount_units),
    );
    if (matches.length === 1) {
      const authorizations = (proof.authorizations ?? []).filter(
        (authorization) =>
          authorization.token.toLowerCase() === intent.token.toLowerCase() &&
          authorization.authorizer.toLowerCase() ===
            intent.buyer_wallet_address.toLowerCase() &&
          authorization.nonce.toLowerCase() === intent.authorization_nonce?.toLowerCase(),
      );
      if (authorizations.length !== 1) {
        await this.#recordCheck(purchaseId, intent, {
          reference,
          outcome: "AMBIGUOUS_TRANSFER_EVIDENCE",
          confirmations,
          receiptStatus: 1,
          ...(proof.blockNumber === undefined ? {} : { blockNumber: proof.blockNumber }),
          authorizationState: "MISSING_OR_AMBIGUOUS",
        });
        return this.#evidence.reconciliation(
          purchaseId,
          "independent receipt lacks one matching AuthorizationUsed event",
          evmHash,
        );
      }
      if (receipt.kind !== "EVM") {
        await this.#recordCheck(purchaseId, intent, {
          reference,
          outcome: "AMBIGUOUS_TRANSFER_EVIDENCE",
          confirmations,
          receiptStatus: 1,
        });
        return this.#evidence.reconciliation(
          purchaseId,
          "a synthetic proof cannot settle an on-chain payment intent",
          evmHash,
        );
      }
      const transfer = matches[0]!;
      return this.#evidence.settle({
        purchaseId,
        transactionHash: receipt.reference.hash,
        blockNumber: receipt.proof.blockNumber,
        transferLogIndex: transfer.logIndex,
        receiptStatus: proof.status,
        token: transfer.token,
        fromAddress: transfer.from,
        toAddress: transfer.to,
        amountUnits: Number(transfer.amount),
      });
    }
    if (buyerOutflows.length === 1) {
      return this.#classifyMismatch(purchaseId, intent, receipt, buyerOutflows[0]!, evmHash);
    }
    if (buyerOutflows.length === 0) {
      return this.#classifyNoTransfer(purchaseId, intent, receipt, evmHash);
    }
    await this.#recordCheck(purchaseId, intent, {
      reference,
      outcome: "AMBIGUOUS_TRANSFER_EVIDENCE",
      confirmations,
      receiptStatus: 1,
      ...(proof.blockNumber === undefined ? {} : { blockNumber: proof.blockNumber }),
    });
    return this.#evidence.reconciliation(
      purchaseId,
      "independent receipt has more than one buyer outflow",
      evmHash,
    );
  }

  /** A verified buyer outflow that contradicts the quote is a terminal mismatch. */
  async #classifyMismatch(
    purchaseId: string,
    intent: PaymentIntent,
    receipt: ClassifiedReceipt,
    outflow: ReceiptTransfer,
    evmHash: Hex | undefined,
  ): Promise<PaymentIntent> {
    const reference =
      receipt.kind === "EVM"
        ? evmTransactionRef({
            hash: receipt.reference.hash,
            evidenceSource: receipt.reference.evidenceSource,
            blockNumber: receipt.proof.blockNumber,
            logIndex: outflow.logIndex,
          })
        : receipt.reference;
    await this.#recordCheck(purchaseId, intent, {
      reference,
      outcome: "MISMATCHED_TRANSFER_CONFIRMED",
      confirmations: receipt.confirmations,
      receiptStatus: 1,
      ...(receipt.proof.blockNumber === undefined
        ? {}
        : { blockNumber: receipt.proof.blockNumber }),
    });
    if (this.#terminal === null) {
      return this.#evidence.reconciliation(
        purchaseId,
        "independent receipt lacks one exact ERC-20 Transfer",
        evmHash,
      );
    }
    const submissionRef = reference.kind === "LOCAL" ? reference.id : reference.hash;
    const proof: ConfirmedMismatchProof = {
      purchaseId,
      actualTransfer: {
        amountUnits: Number(outflow.amount),
        token: outflow.token,
        from: outflow.from,
        to: outflow.to,
      },
      transactionRef: reference,
      proofRef: sha256Ref(`${submissionRef}:${outflow.logIndex}:mismatch`),
      evidenceSource:
        reference.kind === "LOCAL" ? "SYNTHETIC_LOCAL" : reference.evidenceSource,
    };
    return this.#terminal.confirmMismatch(proof);
  }

  /**
   * H3: a success proof without any buyer outflow only closes as terminal no-transfer once
   * the bounded attempt budget AND the configured finality depth are both satisfied.
   * Zero or unknown confirmations always stay `PAYMENT_CONFIRMATION_UNKNOWN`.
   */
  async #classifyNoTransfer(
    purchaseId: string,
    intent: PaymentIntent,
    receipt: ClassifiedReceipt,
    evmHash: Hex | undefined,
  ): Promise<PaymentIntent> {
    const reference = receipt.reference;
    const checked = await this.#recordCheck(purchaseId, intent, {
      reference,
      outcome: "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
      confirmations: receipt.confirmations,
      receiptStatus: 1,
      ...(receipt.proof.blockNumber === undefined
        ? {}
        : { blockNumber: receipt.proof.blockNumber }),
    });
    const attempts = checked.reconciliation_attempt_count ?? 0;
    const finalityReached =
      receipt.confirmations >= this.#minimumFinalityConfirmations;
    if (
      this.#terminal === null ||
      attempts < this.#boundedReconciliationAttempts ||
      !finalityReached
    ) {
      return this.#evidence.reconciliation(
        purchaseId,
        finalityReached
          ? "independent receipt lacks one exact ERC-20 Transfer"
          : "independent proof has not reached the configured finality depth",
        evmHash,
      );
    }
    const submissionRef = reference.kind === "LOCAL" ? reference.id : reference.hash;
    const firstCheckedAt = checked.reconciliation_first_checked_at ?? null;
    const lastCheckedAt = checked.reconciliation_last_checked_at ?? null;
    if (firstCheckedAt === null || lastCheckedAt === null) {
      return this.#evidence.reconciliation(
        purchaseId,
        "reconciliation attempt evidence is incomplete",
        evmHash,
      );
    }
    const proof: NoTransferProof = {
      purchaseId,
      reasonCode: "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
      checkedChainId: BASE_SEPOLIA_CHAIN_ID,
      attemptCount: attempts,
      firstCheckedAt,
      lastCheckedAt,
      authorizationNonceHash: sha256Ref(intent.authorization_nonce ?? intent.quote_id),
      finalityEvidence: { confirmations: receipt.confirmations },
      proofRef: sha256Ref(`${submissionRef}:${attempts}:no-transfer`),
      evidenceSource:
        reference.kind === "LOCAL" ? "SYNTHETIC_LOCAL" : reference.evidenceSource,
      submissionRef,
    };
    return this.#terminal.reconcileNoTransfer(proof);
  }

  #assertViewIntent(
    view: {
      purchase_id: string;
      decision_event_hash: PaymentIntent["decision_event_hash"];
      buyer_wallet_address: Address;
      quote: { quote_id: string; amount_units: number; token: Address; pay_to: Address };
    },
    intent: PaymentIntent,
  ): void {
    if (
      view.purchase_id !== intent.purchase_id ||
      view.decision_event_hash !== intent.decision_event_hash ||
      view.buyer_wallet_address.toLowerCase() !==
        intent.buyer_wallet_address.toLowerCase() ||
      view.quote.quote_id !== intent.quote_id ||
      view.quote.amount_units !== intent.amount_units ||
      view.quote.token.toLowerCase() !== intent.token.toLowerCase() ||
      view.quote.pay_to.toLowerCase() !== intent.pay_to.toLowerCase()
    ) {
      throw new CommerceGatewayError("claimed payment intent changed decision binding");
    }
  }
}
