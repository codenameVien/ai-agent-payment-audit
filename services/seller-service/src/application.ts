import { createHash, randomUUID } from "node:crypto";

import type {
  PaymentGate,
  PaymentSettlement,
  ProviderResult,
  QuoteRequest,
  SignedSellerQuote,
  SellerExecutionRecord,
  SellerExecutionStore,
} from "./contracts.js";
import type { SellerEngine } from "./seller-engine.js";

export class SellerApplication {
  readonly #engine: SellerEngine;
  readonly #payments: PaymentGate;
  readonly #store?: SellerExecutionStore;
  readonly #executions = new Map<
    string,
    {
      prompt: string;
      paymentProofHash: string;
      promise: Promise<{
        result?: ProviderResult;
        settlement: PaymentSettlement;
        providerError?: string;
      }>;
    }
  >();

  constructor(engine: SellerEngine, payments: PaymentGate, store?: SellerExecutionStore) {
    this.#engine = engine;
    this.#payments = payments;
    this.#store = store;
  }

  quote(request: QuoteRequest): Promise<SignedSellerQuote> {
    return this.#engine.quote(request);
  }

  async paidInference(args: {
    purchaseId: string;
    quoteId: string;
    prompt: string;
    paymentSignature?: string;
  }): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }> {
    if (!args.paymentSignature) throw new Error("payment required");
    const key = `${args.purchaseId}:${args.quoteId}`;
    const paymentProofHash = createHash("sha256")
      .update(args.paymentSignature)
      .digest("hex");
    const boundPaymentProofHash = `sha256:${paymentProofHash}`;
    const existing = this.#executions.get(key);
    if (existing !== undefined) {
      if (
        existing.prompt !== args.prompt ||
        existing.paymentProofHash !== boundPaymentProofHash
      ) {
        throw new Error("payment quote replay changed the inference binding");
      }
      return existing.promise;
    }
    const promise = this.#store === undefined
      ? this.#execute(args)
      : this.#executeDurable(args, boundPaymentProofHash);
    this.#executions.set(key, {
      prompt: args.prompt,
      paymentProofHash: boundPaymentProofHash,
      promise,
    });
    try {
      return await promise;
    } catch (error) {
      this.#executions.delete(key);
      throw error;
    }
  }

  async recover(purchaseId: string, quoteId: string): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }> {
    if (this.#store === undefined) throw new Error("durable seller recovery unavailable");
    const record = await this.#store.get(purchaseId);
    if (record === null || record.quoteId !== quoteId) {
      throw new Error("seller execution not found");
    }
    return this.#resumeDurable(record);
  }

  async #executeDurable(
    args: {
      purchaseId: string;
      quoteId: string;
      prompt: string;
      paymentSignature?: string;
    },
    paymentProofHash: string,
  ): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }> {
    const record = await this.#store!.claim({
      purchaseId: args.purchaseId,
      quoteId: args.quoteId,
      sellerAgentId: this.#engine.sellerAgentId,
      prompt: args.prompt,
      promptHash: `sha256:${createHash("sha256").update(args.prompt).digest("hex")}`,
      paymentProofHash,
    });
    if (
      record.prompt !== args.prompt ||
      record.paymentProofHash !== paymentProofHash
    ) {
      throw new Error("durable seller execution binding mismatch");
    }
    return this.#resumeDurable(record, args.paymentSignature);
  }

  async #resumeDurable(
    initial: SellerExecutionRecord,
    paymentSignature?: string,
  ): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }> {
    let record = initial;
    if (record.state === "CLAIMED") {
      if (!paymentSignature) throw new Error("payment proof is required to resume authorization");
      const authorization = await this.#payments.authorize({
        purchaseId: record.purchaseId,
        quoteId: record.quoteId,
        paymentSignature,
      });
      if (
        authorization.purchaseId !== record.purchaseId ||
        authorization.quoteId !== record.quoteId
      ) throw new Error("payment authorization binding mismatch");
      record = await this.#store!.recordAuthorization(record.purchaseId, authorization);
    }
    if (record.state === "SUBMITTED") {
      if (record.authorization === undefined) {
        throw new Error("durable payment authorization is missing");
      }
      const settlement = await this.#payments.settle(record.authorization);
      record = await this.#store!.recordSettlement(record.purchaseId, settlement);
    }
    if (record.state === "SETTLED") {
      if (record.settlement === undefined) {
        throw new Error("durable payment settlement is missing");
      }
      if (!record.settlement.success) return { settlement: record.settlement };
      if (record.authorization === undefined) {
        throw new Error("durable payment authorization is missing");
      }
      const confirmedSettlement = record.settlement;
      const providerModelId = record.authorization.modelId;
      const providerAttemptId = `sha256:${createHash("sha256")
        .update([
          record.purchaseId,
          record.quoteId,
          record.promptHash,
          providerModelId,
        ].join(":"))
        .digest("hex")}`;
      const providerAttemptToken = randomUUID();
      record = await this.#store!.recordProviderSubmission(
        record.purchaseId,
        providerAttemptId,
        providerAttemptToken,
      );
      if (
        record.state !== "PROVIDER_SUBMITTED" ||
        record.providerAttemptId !== providerAttemptId
      ) {
        if (record.state !== "DELIVERED") {
          throw new Error("durable provider attempt binding changed");
        }
      } else if (record.providerAttemptToken === providerAttemptToken) {
        let result: ProviderResult;
        try {
          result = await this.#engine.infer(
            providerModelId,
            record.prompt,
          );
        } catch (error) {
          return {
            settlement: confirmedSettlement,
            providerError:
              error instanceof Error ? error.message : "provider inference failed",
          };
        }
        try {
          record = await this.#store!.recordResult(record.purchaseId, result);
        } catch {
          return {
            settlement: confirmedSettlement,
            providerError:
              "provider result persistence failed; delivery outcome requires reconciliation",
          };
        }
      }
    }
    if (record.state === "PROVIDER_SUBMITTED") {
      if (record.settlement === undefined || record.providerAttemptId === undefined) {
        throw new Error("durable provider attempt is incomplete");
      }
      return {
        settlement: record.settlement,
        providerError: "provider delivery outcome requires reconciliation",
      };
    }
    if (
      record.state !== "DELIVERED" ||
      record.settlement === undefined ||
      record.result === undefined
    ) {
      throw new Error("durable seller execution is incomplete");
    }
    return { settlement: record.settlement, result: record.result };
  }

  async #execute(args: {
    purchaseId: string;
    quoteId: string;
    prompt: string;
    paymentSignature?: string;
  }): Promise<{
    result?: ProviderResult;
    settlement: PaymentSettlement;
    providerError?: string;
  }> {
    const authorization = await this.#payments.authorize({
      purchaseId: args.purchaseId,
      quoteId: args.quoteId,
      paymentSignature: args.paymentSignature,
    });
    if (
      authorization.purchaseId !== args.purchaseId ||
      authorization.quoteId !== args.quoteId
    ) {
      throw new Error("payment authorization binding mismatch");
    }
    const settlement = await this.#payments.settle(authorization);
    if (!settlement.success) return { settlement };
    try {
      const result = await this.#engine.infer(authorization.modelId, args.prompt);
      return { result, settlement };
    } catch (error) {
      return {
        settlement,
        providerError: error instanceof Error ? error.message : "provider inference failed",
      };
    }
  }
}

export class RejectAllPayments implements PaymentGate {
  async authorize(): Promise<never> {
    throw new Error("payment required");
  }

  async settle(): Promise<never> {
    throw new Error("payment required");
  }
}
