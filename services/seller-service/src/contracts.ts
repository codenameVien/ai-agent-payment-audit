import type { Address, Hex } from "viem";

export interface QuoteRequest {
  purchaseId: string;
  requestId: string;
  minInputLimit: number;
  minOutputLimit: number;
  maxLatencyMs?: number;
  preferredModelId?: string;
}

export interface ModelOffer {
  modelId: string;
  modelVersion: string;
  amount: bigint;
  expectedLatencyMs: number;
  inputLimit: number;
  outputLimit: number;
  enabled: boolean;
}

export interface SellerConfig {
  sellerAgentId: string;
  erc8004AgentId: bigint;
  providerId: string;
  agentWallet: Address;
  token: Address;
  payTo: Address;
  quoteLifetimeSeconds: number;
  models: readonly ModelOffer[];
}

export interface PaymentQuoteTerms {
  quoteId: string;
  purchaseId: string;
  sellerAgentId: string;
  erc8004AgentId: bigint;
  amount: bigint;
  token: Address;
  payTo: Address;
  expiresAt: bigint;
  quoteNonce: bigint;
  counterofferOf: string;
}

export interface AiInferenceQuoteExtension {
  providerId: string;
  modelId: string;
  modelVersion: string;
  expectedLatencyMs: bigint;
  inputLimit: bigint;
  outputLimit: bigint;
  available: boolean;
  counterofferReason: string;
}

export interface SellerQuote extends PaymentQuoteTerms, AiInferenceQuoteExtension {}

export interface SignedSellerQuote {
  quote: SellerQuote;
  signature: Hex;
  signer: Address;
}

export interface ProviderResult {
  providerId: string;
  modelId: string;
  modelVersion: string;
  responseId: string;
  text: string;
}

export interface ProviderAdapter {
  readonly providerId: string;
  generate(modelId: string, prompt: string): Promise<ProviderResult>;
}

export interface PaymentAuthorization {
  purchaseId: string;
  quoteId: string;
  modelId: string;
  settlementContext: unknown;
}

export interface PaymentSettlement {
  success: boolean;
  transaction: string;
  network: string;
  payer?: string;
  errorReason?: string;
}

export interface PaymentGate {
  authorize(
    proof: { purchaseId: string; quoteId: string; paymentSignature?: string },
  ): Promise<PaymentAuthorization>;
  settle(authorization: PaymentAuthorization): Promise<PaymentSettlement>;
}

export type SellerExecutionState =
  | "CLAIMED"
  | "SUBMITTED"
  | "SETTLED"
  | "PROVIDER_SUBMITTED"
  | "DELIVERED";

export interface SellerExecutionRecord {
  purchaseId: string;
  quoteId: string;
  sellerAgentId: string;
  prompt: string;
  promptHash: string;
  paymentProofHash: string;
  state: SellerExecutionState;
  authorization?: PaymentAuthorization;
  settlement?: PaymentSettlement;
  providerAttemptId?: string;
  providerAttemptToken?: string;
  result?: ProviderResult;
}

export interface SellerExecutionStore {
  claim(args: {
    purchaseId: string;
    quoteId: string;
    sellerAgentId: string;
    prompt: string;
    promptHash: string;
    paymentProofHash: string;
  }): Promise<SellerExecutionRecord>;
  get(purchaseId: string): Promise<SellerExecutionRecord | null>;
  recordAuthorization(
    purchaseId: string,
    authorization: PaymentAuthorization,
  ): Promise<SellerExecutionRecord>;
  recordSettlement(
    purchaseId: string,
    settlement: PaymentSettlement,
  ): Promise<SellerExecutionRecord>;
  recordProviderSubmission(
    purchaseId: string,
    providerAttemptId: string,
    providerAttemptToken: string,
  ): Promise<SellerExecutionRecord>;
  recordResult(
    purchaseId: string,
    result: ProviderResult,
  ): Promise<SellerExecutionRecord>;
}

export interface QuoteSigner {
  readonly address: Address;
  sign(quote: SellerQuote): Promise<Hex>;
}

export interface Clock { nowSeconds(): bigint; }
export interface QuoteIdFactory { next(): string; }
export interface QuoteNonceFactory { next(): bigint; }

export class QuoteUnavailableError extends Error {
  constructor(message = "no model can satisfy the quote request") {
    super(message);
    this.name = "QuoteUnavailableError";
  }
}
