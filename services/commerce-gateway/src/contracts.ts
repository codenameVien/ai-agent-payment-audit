import type { Address, Hex } from "viem";

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
  decision_event_hash: Hex;
  quote: PaymentQuoteView;
  event_count: number;
  head_event_hash: Hex;
}

export type PaymentIntentState =
  | "CLAIMED"
  | "AUTHORIZED"
  | "RECONCILIATION_REQUIRED"
  | "SETTLED"
  | "FAILED";

export interface PaymentIntent {
  purchase_id: string;
  buyer_wallet_address: Address;
  policy_date: string;
  quote_id: string;
  decision_event_hash: Hex;
  amount_units: number;
  token: Address;
  pay_to: Address;
  permit2_nonce: string;
  state: PaymentIntentState;
  claimed_at: string;
  decision_authorization_hash?: Hex | null;
  decision_authorization_signature?: Hex | null;
  transaction_hash?: Hex | null;
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

export interface Permit2Authorization {
  permitted: { token: Address; amount: string };
  from: Address;
  spender: Address;
  nonce: string;
  deadline: string;
  witness: { to: Address; validAfter: string };
}

export interface PaymentPayload {
  x402Version: 2;
  resource: ResourceInfo;
  accepted: PaymentRequirements;
  payload: {
    signature: Hex;
    permit2Authorization: Permit2Authorization;
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

export interface DecisionAuthorization {
  purchaseId: string;
  decisionEventHash: Hex;
  quoteId: string;
  amount: bigint;
  token: Address;
  payTo: Address;
  permit2Nonce: bigint;
}

export interface EvidenceApi {
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
  sign(message: DecisionAuthorization): Promise<{ hash: Hex; signature: Hex }>;
}

export interface Permit2Signer {
  sign(authorization: Permit2Authorization): Promise<Hex>;
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

export interface ReceiptProof {
  transactionHash: Hex;
  status: 0 | 1;
  blockNumber: number;
  transfers: Array<{
    token: Address;
    from: Address;
    to: Address;
    amount: bigint;
    logIndex: number;
  }>;
}

export interface ReceiptReader {
  read(transactionHash: Hex): Promise<ReceiptProof | null>;
}

export interface QuoteIdentityVerifier {
  verifyQuoteSigner(agentId: bigint, signer: Address): Promise<unknown>;
}

export interface Clock {
  nowSeconds(): bigint;
}
