import { createHash } from "node:crypto";

import type { Address, Hex } from "viem";

import {
  BASE_SEPOLIA_CHAIN_ID,
  type Clock,
  type ConfirmedMismatchProof,
  type DecisionSigner,
  type Erc3009Signer,
  type EvidenceApi,
  type EvmTransactionRef,
  type LocalTransactionRef,
  type NoTransferProof,
  type PaymentIntent,
  type PaymentExecutionResult,
  type PaymentView,
  type QuoteIdentityVerifier,
  type ReceiptProof,
  type ReceiptReader,
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

/** Builds the EVM variant, rejecting local identifiers and non-canonical hashes. */
export function evmTransactionRef(args: {
  hash: string;
  evidenceSource?: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
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
  return {
    kind: "EVM",
    hash: args.hash as Hex,
    chainId: BASE_SEPOLIA_CHAIN_ID,
    evidenceSource: args.evidenceSource ?? "BASE_SEPOLIA_VERIFIED",
    ...(args.blockNumber === undefined ? {} : { blockNumber: args.blockNumber }),
    ...(args.logIndex === undefined ? {} : { logIndex: args.logIndex }),
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
    const source = candidate.evidenceSource;
    if (source === "SYNTHETIC_LOCAL") {
      throw new TransactionRefError("synthetic evidence cannot use an EVM transaction reference");
    }
    return evmTransactionRef({
      hash: String(candidate.hash ?? ""),
      evidenceSource:
        source === "HISTORICAL_ON_CHAIN" ? "HISTORICAL_ON_CHAIN" : "BASE_SEPOLIA_VERIFIED",
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

function receiptTransactionRef(receipt: ReceiptProof, logIndex?: number): TransactionRef {
  if (receipt.transactionRef !== undefined) {
    return assertTransactionRef(receipt.transactionRef);
  }
  return evmTransactionRef({
    hash: receipt.transactionHash.toLowerCase(),
    blockNumber: receipt.blockNumber,
    ...(logIndex === undefined ? {} : { logIndex }),
  });
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
  }

  async execute(purchaseId: string, resourceBody?: unknown): Promise<PaymentExecutionResult> {
    const view = await this.#evidence.paymentView(purchaseId);
    await this.#identityVerifier.verifyQuoteSigner(
      BigInt(view.quote.erc8004_agent_id),
      view.quote.signer_address,
    );
    const intent = await this.#evidence.claim(purchaseId);
    this.#assertViewIntent(view, intent);
    if (intent.state === "SETTLED") {
      const staged = await this.#evidence.stagedDelivery(purchaseId);
      return staged === null ? intent : this.#finishStaged(purchaseId, intent, staged);
    }
    if (intent.state === "FAILED") {
      return intent;
    }
    if (intent.transfer_method !== "eip3009") {
      throw new CommerceGatewayError(
        "legacy Permit2 payment intents are read-only and cannot be executed",
      );
    }
    if (intent.state === "RECONCILIATION_REQUIRED" && intent.transaction_hash) {
      const outcome = await this.#reconcileTransaction(
        purchaseId,
        intent,
        intent.transaction_hash,
      );
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
      return this.#reconcileTransaction(purchaseId, submitted, transactionHash);
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
      return this.#reconcileTransaction(purchaseId, submitted, transactionHash);
    }
    const submitted = await this.#recordSubmittedTransaction(
      purchaseId,
      "facilitator returned submitted transaction",
      transactionHash,
      recoveredFromHashless,
    );
    return this.#finishStaged(
      purchaseId,
      await this.#reconcileTransaction(purchaseId, submitted, transactionHash),
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
    if (
      intent.state !== "RECONCILIATION_REQUIRED" ||
      intent.transaction_hash?.toLowerCase() !== transactionHash.toLowerCase()
    ) {
      throw new CommerceGatewayError("transaction hash is not bound to this payment intent");
    }
    return this.#reconcileTransaction(purchaseId, intent, transactionHash);
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
    transactionHash: Hex,
  ): Promise<PaymentIntent> {
    const receipt = await this.#receipts.read(transactionHash);
    if (receipt === null) {
      await this.#recordCheck(purchaseId, intent, {
        reference: evmTransactionRef({ hash: transactionHash.toLowerCase() }),
        outcome: "RECEIPT_NOT_FOUND",
        confirmations: 0,
      });
      return this.#evidence.reconciliation(
        purchaseId,
        `receipt pending for ${transactionHash}`,
        transactionHash,
      );
    }
    if (receipt.transactionHash.toLowerCase() !== transactionHash.toLowerCase()) {
      await this.#recordCheck(purchaseId, intent, {
        reference: evmTransactionRef({ hash: transactionHash.toLowerCase() }),
        outcome: "RECEIPT_HASH_MISMATCH",
        confirmations: receipt.confirmations ?? 0,
      });
      return this.#evidence.reconciliation(
        purchaseId,
        "RPC receipt hash mismatch",
        transactionHash,
      );
    }
    const confirmations = receipt.confirmations ?? 0;
    if (receipt.status === 0) {
      await this.#recordCheck(purchaseId, intent, {
        reference: receiptTransactionRef(receipt),
        outcome: "RECEIPT_REVERTED",
        confirmations,
        receiptStatus: 0,
        blockNumber: receipt.blockNumber,
      });
      return this.#evidence.failConfirmed(
        purchaseId,
        "independent receipt status zero",
        transactionHash,
        receipt.blockNumber,
      );
    }
    const buyerOutflows = receipt.transfers.filter(
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
      const authorizations = (receipt.authorizations ?? []).filter(
        (authorization) =>
          authorization.token.toLowerCase() === intent.token.toLowerCase() &&
          authorization.authorizer.toLowerCase() ===
            intent.buyer_wallet_address.toLowerCase() &&
          authorization.nonce.toLowerCase() === intent.authorization_nonce?.toLowerCase(),
      );
      if (authorizations.length !== 1) {
        await this.#recordCheck(purchaseId, intent, {
          reference: receiptTransactionRef(receipt),
          outcome: "AMBIGUOUS_TRANSFER_EVIDENCE",
          confirmations,
          receiptStatus: 1,
          blockNumber: receipt.blockNumber,
          authorizationState: "MISSING_OR_AMBIGUOUS",
        });
        return this.#evidence.reconciliation(
          purchaseId,
          "independent receipt lacks one matching AuthorizationUsed event",
          transactionHash,
        );
      }
      const transfer = matches[0]!;
      return this.#evidence.settle({
        purchaseId,
        transactionHash,
        blockNumber: receipt.blockNumber,
        transferLogIndex: transfer.logIndex,
        receiptStatus: receipt.status,
        token: transfer.token,
        fromAddress: transfer.from,
        toAddress: transfer.to,
        amountUnits: Number(transfer.amount),
      });
    }
    if (buyerOutflows.length === 1) {
      return this.#classifyMismatch(purchaseId, intent, receipt, buyerOutflows[0]!, {
        confirmations,
        transactionHash,
      });
    }
    if (buyerOutflows.length === 0) {
      return this.#classifyNoTransfer(purchaseId, intent, receipt, {
        confirmations,
        transactionHash,
      });
    }
    await this.#recordCheck(purchaseId, intent, {
      reference: receiptTransactionRef(receipt),
      outcome: "AMBIGUOUS_TRANSFER_EVIDENCE",
      confirmations,
      receiptStatus: 1,
      blockNumber: receipt.blockNumber,
    });
    return this.#evidence.reconciliation(
      purchaseId,
      "independent receipt has more than one buyer outflow",
      transactionHash,
    );
  }

  /** A verified buyer outflow that contradicts the quote is a terminal mismatch. */
  async #classifyMismatch(
    purchaseId: string,
    intent: PaymentIntent,
    receipt: ReceiptProof,
    outflow: { token: Address; from: Address; to: Address; amount: bigint; logIndex: number },
    context: { confirmations: number; transactionHash: Hex },
  ): Promise<PaymentIntent> {
    const reference = receiptTransactionRef(receipt, outflow.logIndex);
    await this.#recordCheck(purchaseId, intent, {
      reference,
      outcome: "MISMATCHED_TRANSFER_CONFIRMED",
      confirmations: context.confirmations,
      receiptStatus: 1,
      blockNumber: receipt.blockNumber,
    });
    if (this.#terminal === null) {
      return this.#evidence.reconciliation(
        purchaseId,
        "independent receipt lacks one exact ERC-20 Transfer",
        context.transactionHash,
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
   * A success receipt without any buyer outflow only closes as terminal no-transfer
   * after the bounded attempt budget is exhausted; otherwise it stays unknown.
   */
  async #classifyNoTransfer(
    purchaseId: string,
    intent: PaymentIntent,
    receipt: ReceiptProof,
    context: { confirmations: number; transactionHash: Hex },
  ): Promise<PaymentIntent> {
    const reference = receiptTransactionRef(receipt);
    const checked = await this.#recordCheck(purchaseId, intent, {
      reference,
      outcome: "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
      confirmations: context.confirmations,
      receiptStatus: 1,
      blockNumber: receipt.blockNumber,
    });
    const attempts = checked.reconciliation_attempt_count ?? 0;
    if (this.#terminal === null || attempts < this.#boundedReconciliationAttempts) {
      return this.#evidence.reconciliation(
        purchaseId,
        "independent receipt lacks one exact ERC-20 Transfer",
        context.transactionHash,
      );
    }
    const submissionRef = reference.kind === "LOCAL" ? reference.id : reference.hash;
    const firstCheckedAt =
      checked.reconciliation_first_checked_at ??
      checked.reconciliation_last_checked_at ??
      new Date(Number(this.#clock.nowSeconds()) * 1000).toISOString();
    const proof: NoTransferProof = {
      purchaseId,
      reasonCode: "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
      checkedChainId: BASE_SEPOLIA_CHAIN_ID,
      attemptCount: attempts,
      firstCheckedAt,
      lastCheckedAt:
        checked.reconciliation_last_checked_at ??
        new Date(Number(this.#clock.nowSeconds()) * 1000).toISOString(),
      authorizationNonceHash: sha256Ref(intent.authorization_nonce ?? intent.quote_id),
      finalityEvidence: { confirmations: context.confirmations },
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
