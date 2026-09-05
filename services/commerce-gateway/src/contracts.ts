import type { Address, Hex } from "viem";

export type EvidenceHash = Hex | `sha256:${string}`;

export interface PaymentQuoteView {
    quote_id: string;
    seller_agent_id: string;
    erc8004_agent_id: string;
  provider_id: string;
  model_id: string;
  model_version: string;
  amount_units: number;
  token: Address;
  pay_to: Address;
  expires_at: string;
  signer_address: Address;
  chain_id: number;
  verifying_contract: Address;
}

export interface PaymentView {
  purchase_id: string;
  owner_address: Address;
  buyer_wallet_address: Address;
  budget_units: number;
  request_policy: Record<string, unknown>;
  decision_event_hash: EvidenceHash;
  quote: PaymentQuoteView;
  event_count: number;
  head_event_hash: EvidenceHash;
}

export type PaymentIntentState =
  | "CLAIMED"
  | "AUTHORIZED"
  | "RECONCILIATION_REQUIRED"
  | "SETTLED"
  | "FAILED"
  | "MISMATCH_CONFIRMED"
  | "RECONCILED_NO_TRANSFER";

/** Every terminal payment state. A terminal intent must never be executed again. */
export const TERMINAL_PAYMENT_INTENT_STATE: Record<PaymentIntentState, boolean> = {
  CLAIMED: false,
  AUTHORIZED: false,
  RECONCILIATION_REQUIRED: false,
  SETTLED: true,
  FAILED: true,
  MISMATCH_CONFIRMED: true,
  RECONCILED_NO_TRANSFER: true,
};

export const BASE_SEPOLIA_CHAIN_ID = 84532;

export type EvidenceSource =
  | "BASE_SEPOLIA_VERIFIED"
  | "HISTORICAL_ON_CHAIN"
  | "SYNTHETIC_LOCAL";

/** A chain-provable transaction. `localtx:` values can never reach this variant. */
export interface EvmTransactionRef {
  kind: "EVM";
  hash: Hex;
  chainId: typeof BASE_SEPOLIA_CHAIN_ID;
  evidenceSource: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
  blockNumber?: number;
  logIndex?: number;
}

/** A synthetic local-ledger transaction. It is never rendered as a chain hash. */
export interface LocalTransactionRef {
  kind: "LOCAL";
  id: string;
  runId: string;
  evidenceSource: "SYNTHETIC_LOCAL";
}

export type TransactionRef = EvmTransactionRef | LocalTransactionRef;

export type ReconciliationVerifierOutcome =
  | "RECEIPT_NOT_FOUND"
  | "RECEIPT_PENDING_FINALITY"
  | "RECEIPT_HASH_MISMATCH"
  | "AMBIGUOUS_TRANSFER_EVIDENCE"
  | "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"
  | "AUTHORIZATION_UNUSED_AFTER_EXPIRY"
  | "MISMATCHED_TRANSFER_CONFIRMED"
  | "RECEIPT_REVERTED"
  | "EXACT_TRANSFER_CONFIRMED";

export interface ReconciliationCheck {
  purchaseId: string;
  attemptNumber: number;
  checkedAt: string;
  checkedChainId: number;
  submissionRef: string;
  verifierOutcome: ReconciliationVerifierOutcome;
  finalityConfirmations: number;
  proofRef: string;
  evidenceSource: EvidenceSource;
  receiptStatus?: 0 | 1;
  blockNumber?: number;
  authorizationState?: string;
}

export interface ActualTransferProof {
  amountUnits: number;
  token: Address;
  from: Address;
  to: Address;
}

export interface ConfirmedMismatchProof {
  purchaseId: string;
  actualTransfer: ActualTransferProof;
  transactionRef: TransactionRef;
  proofRef: string;
  evidenceSource: EvidenceSource;
}

export interface NoTransferProof {
  purchaseId: string;
  reasonCode: string;
  checkedChainId: number;
  attemptCount: number;
  firstCheckedAt: string;
  lastCheckedAt: string;
  authorizationNonceHash: string;
  finalityEvidence: { confirmations: number };
  proofRef: string;
  evidenceSource: EvidenceSource;
  submissionRef?: string;
}

/**
 * Terminal payment operations of the Evidence API. The HTTP adapter that implements
 * them is delivered with the reputation packet, so `EvidenceApi` keeps them optional
 * and the gateway requires an explicit capability before emitting a terminal proof.
 */
export interface TerminalPaymentEvidenceApi {
  recordReconciliationCheck(check: ReconciliationCheck): Promise<PaymentIntent>;
  confirmMismatch(proof: ConfirmedMismatchProof): Promise<PaymentIntent>;
  reconcileNoTransfer(proof: NoTransferProof): Promise<PaymentIntent>;
}

export interface PaymentIntent {
  purchase_id: string;
  buyer_wallet_address: Address;
  policy_date: string;
  quote_id: string;
  decision_event_hash: EvidenceHash;
  amount_units: number;
  token: Address;
  pay_to: Address;
  permit2_nonce?: string | null;
  transfer_method: "permit2" | "eip3009";
  authorization_nonce?: Hex | null;
  state: PaymentIntentState;
  claimed_at: string;
  decision_authorization_hash?: Hex | null;
  decision_authorization_signature?: Hex | null;
  transaction_hash?: Hex | null;
  reconciliation_attempt_count?: number;
  reconciliation_first_checked_at?: string | null;
  reconciliation_last_checked_at?: string | null;
  reconciliation_last_outcome?: ReconciliationVerifierOutcome | null;
  actual_transfer?: ActualTransferProof | null;
  mismatched_fields?: string[];
  terminal_outcome_key?: string | null;
  terminal_proof_ref?: string | null;
  terminal_evidence_source?: EvidenceSource | null;
  local_transaction_id?: string | null;
  no_transfer_reason_code?: string | null;
}

export interface PaymentRequirements {
  scheme: string;
  network: string;
  amount: string;
  asset: Address;
  payTo: Address;
  maxTimeoutSeconds: number;
  extra?: Record<string, unknown>;
}

export interface ResourceInfo {
  url: string;
  description?: string;
  mimeType?: string;
}

export interface PaymentRequired {
  x402Version: number;
  resource: ResourceInfo;
  accepts: PaymentRequirements[];
  extensions?: Record<string, unknown>;
}

export interface Erc3009Authorization {
  from: Address;
  to: Address;
  value: string;
  validAfter: string;
  validBefore: string;
  nonce: Hex;
}

export interface PaymentPayload {
  x402Version: 2;
  resource: ResourceInfo;
  accepted: PaymentRequirements;
  payload: {
    signature: Hex;
    authorization: Erc3009Authorization;
  };
  extensions: Record<string, unknown>;
}

export interface SettlementResponse {
  success: boolean;
  transaction: string;
  network: string;
  payer?: string;
  amount?: string;
  errorReason?: string;
}

export interface Erc3009DecisionAuthorization {
  purchaseId: string;
  decisionEventHash: Hex;
  quoteId: string;
  amount: bigint;
  token: Address;
  payTo: Address;
  authorizationNonce: Hex;
}

export interface EvidenceApi extends Partial<TerminalPaymentEvidenceApi> {
  paymentView(purchaseId: string): Promise<PaymentView>;
  claim(purchaseId: string): Promise<PaymentIntent>;
  authorize(args: {
    purchaseId: string;
    authorizationHash: Hex;
    signature: Hex;
  }): Promise<PaymentIntent>;
  reconciliation(
    purchaseId: string,
    reason: string,
    transactionHash?: Hex,
  ): Promise<PaymentIntent>;
  bindReconciliationTransaction(
    purchaseId: string,
    transactionHash: Hex,
  ): Promise<PaymentIntent>;
  settle(args: {
    purchaseId: string;
    transactionHash: Hex;
    blockNumber: number;
    transferLogIndex: number;
    receiptStatus: number;
    token: Address;
    fromAddress: Address;
    toAddress: Address;
    amountUnits: number;
  }): Promise<PaymentIntent>;
  failConfirmed(
    purchaseId: string,
    reason: string,
    transactionHash: Hex,
    blockNumber: number,
  ): Promise<PaymentIntent>;
  recordDelivery(args: {
    purchaseId: string;
    sellerAgentId: string;
    providerId: string;
    responseId: string;
    responseHash: string;
    modelId: string;
    modelVersion: string;
  }): Promise<void>;
  stageDelivery(args: {
    purchaseId: string;
    sellerAgentId: string;
    providerId: string;
    responseId: string;
    modelId: string;
    modelVersion: string;
    text: string;
  }): Promise<StagedDelivery>;
  stagedDelivery(purchaseId: string): Promise<StagedDelivery | null>;
}

export interface PaymentExecutionResult extends PaymentIntent {
  provider_result?: unknown;
}

export interface StagedDelivery {
  seller_agent_id: string;
  provider_id: string;
  response_id: string;
  response_hash: string;
  model_id: string;
  model_version: string;
  text: string;
}

export interface DecisionSigner {
  signErc3009(
    message: Erc3009DecisionAuthorization,
  ): Promise<{ hash: Hex; signature: Hex }>;
}

export interface Erc3009Signer {
  sign(args: {
    token: Address;
    tokenName: string;
    tokenVersion: string;
    authorization: Erc3009Authorization;
  }): Promise<Hex>;
}

export interface SellerResponse {
  status: number;
  paymentRequired?: string;
  paymentResponse?: string;
  body?: unknown;
}

export interface SellerClient {
  request(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
    paymentSignature?: string;
    resourceBody?: unknown;
  }): Promise<SellerResponse>;
  recover(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
  }): Promise<SellerResponse>;
}

export interface ReceiptTransfer {
  token: Address;
  from: Address;
  to: Address;
  amount: bigint;
  logIndex: number;
}

export interface ReceiptAuthorization {
  token: Address;
  authorizer: Address;
  nonce: Hex;
  logIndex: number;
}

interface ReceiptProofBase {
  status: 0 | 1;
  transfers: ReceiptTransfer[];
  authorizations?: ReceiptAuthorization[];
  /** Observed finality depth. Absent means "unknown", which can never close a payment. */
  confirmations?: number;
}

/** A receipt read from a chain. It can never carry a synthetic identity. */
export interface EvmReceiptProof extends ReceiptProofBase {
  transactionHash: Hex;
  blockNumber: number;
  evidenceSource?: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
  localTransactionId?: never;
  runId?: never;
}

/**
 * A proof produced by an attested synthetic verifier. It has no transaction hash at all,
 * so a local ledger result can never be fabricated into chain evidence.
 */
export interface LocalReceiptProof extends ReceiptProofBase {
  localTransactionId: string;
  runId: string;
  blockNumber?: number;
  evidenceSource?: "SYNTHETIC_LOCAL";
  transactionHash?: never;
}

export type ReceiptProof = EvmReceiptProof | LocalReceiptProof;

export interface ReceiptReader {
  /** `submissionRef` is an EVM transaction hash or a `localtx:` submission identity. */
  read(submissionRef: string): Promise<ReceiptProof | null>;
}

export interface QuoteIdentityVerifier {
  verifyQuoteSigner(agentId: bigint, signer: Address): Promise<unknown>;
}

export interface Clock {
  nowSeconds(): bigint;
}

/** The only tag pair PBL ever writes to or reads from the ERC-8004 reputation registry. */
export const FEEDBACK_TAG1 = "pbl-audit";
export const FEEDBACK_TAG2 = "payment-outcome";

export type ReputationOutboxStatus =
  | "PENDING"
  | "LEASED"
  | "PREPARED"
  | "SUBMITTED_UNKNOWN"
  | "CONFIRMED"
  | "DEFERRED"
  | "CONFLICT";

/** The immutable target of a reputation publication. Its hash is the outbox dedupe key. */
export interface ReputationPublishIdentity {
  chainId: number;
  registryAddress: Address;
  purchaseId: string;
  sellerAgentId: string;
  tag1: string;
  tag2: string;
}

export interface ReputationDecisionView {
  decision: "PUBLISH" | "DEFER";
  value: number | null;
  reasonCodes: string[];
  auditBundleHash: string;
  rulesetVersion: string;
  sellerAgentId: string;
  erc8004AgentId: string;
}

/** Durable outbox state. The publisher owns no state of its own. */
export interface ReputationPublishJob {
  jobId: string;
  status: ReputationOutboxStatus;
  identity: ReputationPublishIdentity;
  identityHash: string;
  payloadFingerprint: string;
  decision: ReputationDecisionView;
  attemptCount: number;
  workerId: string | null;
  leaseExpiresAt: string | null;
  feedbackHash: Hex | null;
  transactionRef: TransactionRef | null;
  receiptProofRef: string | null;
  /** The feedback client and audit-bundle URI committed at PREPARED, before any broadcast. */
  clientAddress: string | null;
  feedbackUri: string | null;
  blockNumber: number | null;
  logIndex: number | null;
  evidenceSource: EvidenceSource | null;
  confirmedProof: ConfirmedFeedbackProof | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * A publication is only confirmed by a receipt plus a decoded `NewFeedback` event.
 * A bare transaction hash can never construct this proof.
 */
export interface ConfirmedFeedbackProof {
  transactionRef: TransactionRef;
  /** The registry the receipt was actually read against, so a proof cannot float between registries. */
  registryAddress: Address;
  receiptProofRef: string;
  clientAddress: Address;
  erc8004AgentId: string;
  value: 0 | 100;
  valueDecimals: 0;
  feedbackHash: Hex;
  blockNumber: number;
  logIndex: number;
  tag1: string;
  tag2: string;
  feedbackUri: string;
}

export interface FeedbackQueryScope {
  chainId: number;
  registryAddress: Address;
  erc8004AgentId: string;
  trustedClients: readonly Address[];
  tag1: string;
  tag2: string;
  fromBlock: number;
  toBlock: number;
}

export interface RawFeedbackEvent {
  value: number;
  valueDecimals: number;
  clientAddress: Address;
  blockNumber: number;
  logIndex: number;
  transactionRef: TransactionRef;
  tag1: string;
  tag2: string;
}

export interface FeedbackQueryResult {
  scope: FeedbackQueryScope;
  /** The head the gateway resolved the range against. */
  latestBlock: number;
  queriedAt: string;
  events: RawFeedbackEvent[];
}

/** Durable exactly-once state for reputation publication. Leases and status live here, never in memory. */
export interface ReputationOutboxApi {
  claim(args: { workerId: string; leaseSeconds: number }): Promise<ReputationPublishJob | null>;
  get(jobId: string): Promise<ReputationPublishJob | null>;
  markPrepared(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    /** Absent before broadcast: an EVM hash only exists once the submission does. */
    transactionRef?: TransactionRef;
    feedbackHash: Hex;
    /** PREPARED is the full pre-broadcast commitment, not just the hash. */
    clientAddress: Address;
    feedbackUri: string;
  }): Promise<ReputationPublishJob>;
  markSubmittedUnknown(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    /**
     * Absent when an external effect may exist and cannot be named: the fail-closed
     * reconciliation state records the doubt instead of inventing a reference.
     */
    transactionRef?: TransactionRef;
    reason: string;
  }): Promise<ReputationPublishJob>;
  markConfirmed(args: {
    jobId: string;
    workerId: string;
    payloadFingerprint: string;
    proof: ConfirmedFeedbackProof;
  }): Promise<ReputationPublishJob>;
  /** Records that a second payload claimed one publish identity. Terminal for the loser. */
  recordConflict(args: {
    identityHash: string;
    requestedFingerprint: string;
    reasonCode: string;
  }): Promise<ReputationPublishJob>;
}
