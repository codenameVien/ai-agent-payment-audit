import type { Address, Hex } from "viem";

import type {
  Clock,
  DecisionAuthorization,
  DecisionSigner,
  Erc3009Signer,
  EvidenceApi,
  PaymentIntent,
  PaymentExecutionResult,
  PaymentView,
  Permit2Signer,
  QuoteIdentityVerifier,
  ReceiptReader,
  SellerClient,
  StagedDelivery,
} from "./contracts.js";
import { digestToBytes32 } from "./digest.js";
import {
  BASE_SEPOLIA_NETWORK,
  PBLC_TOKEN_NAME,
  PBLC_V2_TOKEN_VERSION,
  createErc3009Authorization,
  createErc3009PaymentPayload,
  createEip2612GasSponsoringPayloadExtension,
  createPaymentPayload,
  createPermit2Authorization,
  decodePaymentRequired,
  decodeSettlementResponse,
  encodeHeader,
  selectBoundRequirement,
  X402BindingError,
} from "./x402.js";

export class CommerceGatewayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CommerceGatewayError";
  }
}

function evidenceHashToBytes32(value: string): Hex {
  try {
    return digestToBytes32(value);
  } catch {
    throw new CommerceGatewayError("decision evidence hash is not a bytes32 SHA-256 digest");
  }
}

export class CommerceGateway {
  readonly #evidence: EvidenceApi;
  readonly #seller: SellerClient;
  readonly #decisionSigner: DecisionSigner;
  readonly #permit2Signer: Permit2Signer;
  readonly #erc3009Signer?: Erc3009Signer;
  readonly #receipts: ReceiptReader;
  readonly #identityVerifier: QuoteIdentityVerifier;
  readonly #clock: Clock;

  constructor(args: {
    evidence: EvidenceApi;
    seller: SellerClient;
    decisionSigner: DecisionSigner;
    permit2Signer: Permit2Signer;
    erc3009Signer?: Erc3009Signer;
    receipts: ReceiptReader;
    identityVerifier: QuoteIdentityVerifier;
    clock: Clock;
  }) {
    this.#evidence = args.evidence;
    this.#seller = args.seller;
    this.#decisionSigner = args.decisionSigner;
    this.#permit2Signer = args.permit2Signer;
    this.#erc3009Signer = args.erc3009Signer;
    this.#receipts = args.receipts;
    this.#identityVerifier = args.identityVerifier;
    this.#clock = args.clock;
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
    let signedDecision;
    if (intent.transfer_method === "eip3009") {
      if (
        this.#decisionSigner.signErc3009 === undefined ||
        intent.authorization_nonce === undefined ||
        intent.authorization_nonce === null
      ) {
        throw new CommerceGatewayError("ERC-3009 decision signer or nonce is unavailable");
      }
      signedDecision = await this.#decisionSigner.signErc3009({
        ...commonDecision,
        authorizationNonce: intent.authorization_nonce,
      });
    } else {
      const decision: DecisionAuthorization = {
        ...commonDecision,
        permit2Nonce: BigInt(intent.permit2_nonce),
      };
      signedDecision = await this.#decisionSigner.sign(decision);
    }
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
    let payload;
    if (intent.transfer_method === "eip3009") {
      if (this.#erc3009Signer === undefined) {
        throw new CommerceGatewayError("ERC-3009 signer is unavailable");
      }
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
      payload = createErc3009PaymentPayload({
        resource: required.resource,
        requirement,
        authorization,
        signature,
      });
    } else {
      const permit2Authorization = createPermit2Authorization({
        intent,
        validAfter: now,
        deadline,
      });
      const permitSignature = await this.#permit2Signer.sign(permit2Authorization);
      const paymentExtensions = await createEip2612GasSponsoringPayloadExtension({
        declaredExtensions: required.extensions,
        requirement,
        authorization: permit2Authorization,
        signer: this.#permit2Signer,
      });
      payload = createPaymentPayload({
        resource: required.resource,
        requirement,
        authorization: permit2Authorization,
        signature: permitSignature,
        extensions: paymentExtensions,
      });
    }
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
    if (
      intent.state !== "RECONCILIATION_REQUIRED" ||
      intent.transaction_hash?.toLowerCase() !== transactionHash.toLowerCase()
    ) {
      throw new CommerceGatewayError("transaction hash is not bound to this payment intent");
    }
    return this.#reconcileTransaction(purchaseId, intent, transactionHash);
  }

  async #reconcileTransaction(
    purchaseId: string,
    intent: PaymentIntent,
    transactionHash: Hex,
  ): Promise<PaymentIntent> {
    const receipt = await this.#receipts.read(transactionHash);
    if (receipt === null) {
      return this.#evidence.reconciliation(
        purchaseId,
        `receipt pending for ${transactionHash}`,
        transactionHash,
      );
    }
    if (receipt.transactionHash.toLowerCase() !== transactionHash.toLowerCase()) {
      return this.#evidence.reconciliation(
        purchaseId,
        "RPC receipt hash mismatch",
        transactionHash,
      );
    }
    if (receipt.status === 0) {
      return this.#evidence.failConfirmed(
        purchaseId,
        "independent receipt status zero",
        transactionHash,
        receipt.blockNumber,
      );
    }
    const matches = receipt.transfers.filter(
      (transfer) =>
        transfer.token.toLowerCase() === intent.token.toLowerCase() &&
        transfer.from.toLowerCase() === intent.buyer_wallet_address.toLowerCase() &&
        transfer.to.toLowerCase() === intent.pay_to.toLowerCase() &&
        transfer.amount === BigInt(intent.amount_units),
    );
    if (matches.length !== 1) {
      return this.#evidence.reconciliation(
        purchaseId,
        "independent receipt lacks one exact ERC-20 Transfer",
        transactionHash,
      );
    }
    if (intent.transfer_method === "eip3009") {
      const authorizations = (receipt.authorizations ?? []).filter(
        (authorization) =>
          authorization.token.toLowerCase() === intent.token.toLowerCase() &&
          authorization.authorizer.toLowerCase() ===
            intent.buyer_wallet_address.toLowerCase() &&
          authorization.nonce.toLowerCase() === intent.authorization_nonce?.toLowerCase(),
      );
      if (authorizations.length !== 1) {
        return this.#evidence.reconciliation(
          purchaseId,
          "independent receipt lacks one matching AuthorizationUsed event",
          transactionHash,
        );
      }
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
