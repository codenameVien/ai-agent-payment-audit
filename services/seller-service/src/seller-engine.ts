import type {
  Clock, ModelOffer, ProviderAdapter, ProviderResult, QuoteIdFactory, QuoteNonceFactory,
  QuoteRequest, QuoteSigner, SellerConfig, SellerQuote, SignedSellerQuote,
} from "./contracts.js";
import { QuoteUnavailableError } from "./contracts.js";

export class SystemClock implements Clock {
  nowSeconds(): bigint { return BigInt(Math.floor(Date.now() / 1_000)); }
}

function satisfies(offer: ModelOffer, request: QuoteRequest): boolean {
  return (
    offer.enabled &&
    offer.inputLimit >= request.minInputLimit &&
    offer.outputLimit >= request.minOutputLimit &&
    (request.maxLatencyMs === undefined || offer.expectedLatencyMs <= request.maxLatencyMs)
  );
}

export class SellerEngine {
  readonly sellerAgentId: string;
  readonly #config: SellerConfig;
  readonly #provider: ProviderAdapter;
  readonly #signer: QuoteSigner;
  readonly #clock: Clock;
  readonly #quoteIds: QuoteIdFactory;
  readonly #nonces: QuoteNonceFactory;
  readonly #quoteCache = new Map<
    string,
    { fingerprint: string; result: Promise<SignedSellerQuote> }
  >();

  constructor(args: {
    config: SellerConfig; provider: ProviderAdapter; signer: QuoteSigner; clock: Clock;
    quoteIds: QuoteIdFactory; nonces: QuoteNonceFactory;
  }) {
    this.sellerAgentId = args.config.sellerAgentId;
    if (args.provider.providerId !== args.config.providerId) {
      throw new Error("provider adapter does not match seller config");
    }
    if (args.signer.address.toLowerCase() !== args.config.agentWallet.toLowerCase()) {
      throw new Error("quote signer does not match seller agent wallet");
    }
    this.#config = args.config;
    this.#provider = args.provider;
    this.#signer = args.signer;
    this.#clock = args.clock;
    this.#quoteIds = args.quoteIds;
    this.#nonces = args.nonces;
  }

  async quote(request: QuoteRequest): Promise<SignedSellerQuote> {
    const fingerprint = JSON.stringify(request);
    const cached = this.#quoteCache.get(request.purchaseId);
    if (cached !== undefined) {
      if (cached.fingerprint !== fingerprint) {
        throw new Error("purchase already quoted with different constraints");
      }
      return cached.result;
    }
    const entry = {
      fingerprint,
      result: this.#createQuote(request),
    };
    this.#quoteCache.set(request.purchaseId, entry);
    try {
      return await entry.result;
    } catch (error) {
      if (this.#quoteCache.get(request.purchaseId) === entry) {
        this.#quoteCache.delete(request.purchaseId);
      }
      throw error;
    }
  }

  async read(purchaseId: string, quoteId: string) {
    const cached = this.#quoteCache.get(purchaseId);
    if (cached === undefined) return null;
    const signed = await cached.result;
    if (signed.quote.quoteId !== quoteId) return null;
    return {
      modelId: signed.quote.modelId,
      amount: signed.quote.amount,
      token: signed.quote.token,
      payTo: signed.quote.payTo,
      expiresAt: signed.quote.expiresAt,
    };
  }

  async #createQuote(request: QuoteRequest): Promise<SignedSellerQuote> {
    const preferred = request.preferredModelId
      ? this.#config.models.find((model) => model.modelId === request.preferredModelId)
      : this.#config.models[0];
    const preferredEligible = preferred !== undefined && satisfies(preferred, request);
    const selected = preferredEligible
      ? preferred
      : this.#config.models.find(
          (model) => model.modelId !== preferred?.modelId && satisfies(model, request),
        );
    if (selected === undefined) throw new QuoteUnavailableError();

    const isCounteroffer = !preferredEligible && preferred !== undefined;
    const quote: SellerQuote = {
      quoteId: this.#quoteIds.next(),
      purchaseId: request.purchaseId,
      sellerAgentId: this.#config.sellerAgentId,
      erc8004AgentId: this.#config.erc8004AgentId,
      providerId: this.#config.providerId,
      modelId: selected.modelId,
      modelVersion: selected.modelVersion,
      amount: selected.amount,
      token: this.#config.token,
      payTo: this.#config.payTo,
      expectedLatencyMs: BigInt(selected.expectedLatencyMs),
      inputLimit: BigInt(selected.inputLimit),
      outputLimit: BigInt(selected.outputLimit),
      expiresAt: this.#clock.nowSeconds() + BigInt(this.#config.quoteLifetimeSeconds),
      quoteNonce: this.#nonces.next(),
      counterofferOf: isCounteroffer ? request.requestId : "",
      available: true,
      counterofferReason: isCounteroffer
        ? "preferred model did not satisfy the request constraints"
        : "",
    };
    return {
      quote,
      signature: await this.#signer.sign(quote),
      signer: this.#signer.address,
    };
  }

  async infer(modelId: string, prompt: string): Promise<ProviderResult> {
    const offered = this.#config.models.some(
      (model) => model.modelId === modelId && model.enabled,
    );
    if (!offered) throw new QuoteUnavailableError("model is not enabled by this seller");
    return this.#provider.generate(modelId, prompt);
  }
}
