import assert from "node:assert/strict";
import test from "node:test";
import type { Address, Hex } from "viem";

import { MockProviderAdapter } from "../src/adapters/mock.js";
import { RejectAllPayments, SellerApplication } from "../src/application.js";
import type {
  Clock, ProviderAdapter, QuoteIdFactory, QuoteNonceFactory, QuoteRequest, SellerConfig,
  SellerExecutionRecord, SellerExecutionStore,
} from "../src/contracts.js";
import {
  LocalEip712QuoteSigner, recoverSellerQuoteSigner, verifySellerQuote,
} from "../src/eip712.js";
import { SellerEngine } from "../src/seller-engine.js";

const PRIVATE_KEY =
  "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" as Hex;
const VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000001" as Address;
const TOKEN = "0x0000000000000000000000000000000000000002" as Address;
const PAY_TO = "0x0000000000000000000000000000000000000003" as Address;
const DOMAIN = { chainId: 84532, verifyingContract: VERIFYING_CONTRACT };

class FixedClock implements Clock { nowSeconds(): bigint { return 1_800_000_000n; } }
class SequenceIds implements QuoteIdFactory {
  #value = 0;
  next(): string { this.#value += 1; return `quote-${this.#value}`; }
}
class SequenceNonces implements QuoteNonceFactory {
  #value = 40n;
  next(): bigint { this.#value += 1n; return this.#value; }
}

function request(overrides: Partial<QuoteRequest> = {}): QuoteRequest {
  return {
    purchaseId: "purchase-1",
    requestId: "request-1",
    minInputLimit: 1_000,
    minOutputLimit: 500,
    preferredModelId: "gemini-fast",
    ...overrides,
  };
}

async function fixture(
  configOverrides: Partial<SellerConfig> = {},
  provider: ProviderAdapter = new MockProviderAdapter("gemini"),
) {
  const signer = new LocalEip712QuoteSigner(PRIVATE_KEY, DOMAIN);
  const config: SellerConfig = {
    sellerAgentId: "gemini-agent-8004",
    erc8004AgentId: 1n,
    providerId: "gemini",
    agentWallet: signer.address,
    token: TOKEN,
    payTo: PAY_TO,
    quoteLifetimeSeconds: 120,
    models: [
      {
        modelId: "gemini-fast", modelVersion: "2026-08", amount: 100_000n,
        expectedLatencyMs: 700, inputLimit: 8_000, outputLimit: 2_000, enabled: true,
      },
      {
        modelId: "gemini-large", modelVersion: "2026-08", amount: 200_000n,
        expectedLatencyMs: 1_500, inputLimit: 32_000, outputLimit: 8_000, enabled: true,
      },
    ],
    ...configOverrides,
  };
  return {
    signer,
    engine: new SellerEngine({
      config,
      provider,
      signer,
      clock: new FixedClock(),
      quoteIds: new SequenceIds(),
      nonces: new SequenceNonces(),
    }),
  };
}

test("seller emits a recoverable EIP-712 quote bound to all payment fields", async () => {
  const { engine, signer } = await fixture();
  const signed = await engine.quote(request());
  assert.equal(signed.quote.modelId, "gemini-fast");
  assert.equal(signed.quote.expiresAt, 1_800_000_120n);
  assert.equal(signed.signer, signer.address);
  assert.equal(await recoverSellerQuoteSigner(signed.quote, signed.signature, DOMAIN), signer.address);
  assert.equal(await verifySellerQuote(signed.quote, signed.signature, signer.address, DOMAIN), true);
});

test("tampering amount, token, recipient, expiry, or signer invalidates the quote", async () => {
  const { engine, signer } = await fixture();
  const signed = await engine.quote(request());
  const other = "0x0000000000000000000000000000000000000004" as Address;
  const variants = [
    { ...signed.quote, amount: signed.quote.amount + 1n },
    { ...signed.quote, token: other },
    { ...signed.quote, payTo: other },
    { ...signed.quote, expiresAt: signed.quote.expiresAt + 1n },
    { ...signed.quote, available: false },
    { ...signed.quote, counterofferReason: "tampered" },
  ];
  for (const quote of variants) {
    assert.equal(await verifySellerQuote(quote, signed.signature, signer.address, DOMAIN), false);
  }
  assert.equal(await verifySellerQuote(signed.quote, signed.signature, other, DOMAIN), false);
});

test("seller makes at most one in-provider counteroffer", async () => {
  const { engine } = await fixture();
  const signed = await engine.quote(
    request({ minOutputLimit: 4_000, preferredModelId: "gemini-fast" }),
  );
  assert.equal(signed.quote.modelId, "gemini-large");
  assert.equal(signed.quote.counterofferOf, "request-1");
  assert.match(signed.quote.counterofferReason, /preferred model/);
});

test("seller rejects instead of chaining a second counteroffer", async () => {
  const { engine } = await fixture();
  await assert.rejects(engine.quote(request({ minOutputLimit: 9_000 })), /no model can satisfy/);
});

test("Gemini and Nemotron mocks follow the same provider contract", async () => {
  for (const providerId of ["gemini", "nemotron"]) {
    const result = await new MockProviderAdapter(providerId).generate(`${providerId}-model`, "hello");
    assert.equal(result.providerId, providerId);
    assert.equal(result.text, "mock:hello");
  }
});

test("seller refuses mismatched provider and signing identity", async () => {
  const signer = new LocalEip712QuoteSigner(PRIVATE_KEY, DOMAIN);
  const config: SellerConfig = {
    sellerAgentId: "agent", erc8004AgentId: 1n, providerId: "gemini", agentWallet: signer.address,
    token: TOKEN, payTo: PAY_TO, quoteLifetimeSeconds: 60, models: [],
  };
  const args = {
    config,
    signer,
    clock: new FixedClock(),
    quoteIds: new SequenceIds(),
    nonces: new SequenceNonces(),
  };
  assert.throws(
    () => new SellerEngine({ ...args, provider: new MockProviderAdapter("nemotron") }),
    /provider adapter/,
  );
  assert.throws(
    () => new SellerEngine({
      ...args,
      config: { ...config, agentWallet: PAY_TO },
      provider: new MockProviderAdapter("gemini"),
    }),
    /quote signer/,
  );
});

test("same request retry returns one quote and changed constraints are rejected", async () => {
  const { engine } = await fixture();
  const first = await engine.quote(request());
  const retry = await engine.quote(request());
  assert.strictEqual(retry, first);
  await assert.rejects(
    engine.quote(request({ minOutputLimit: 999 })),
    /purchase already quoted with different constraints/,
  );
});

test("different request ids cannot create two quote lineages for one purchase", async () => {
  const { engine } = await fixture();
  await engine.quote(request());
  await assert.rejects(
    engine.quote(request({ requestId: "request-2" })),
    /purchase already quoted with different constraints/,
  );
});

test("concurrent request ids cannot race into two quote lineages", async () => {
  const { engine } = await fixture();
  const outcomes = await Promise.allSettled([
    engine.quote(request({ requestId: "request-a" })),
    engine.quote(request({ requestId: "request-b" })),
  ]);
  const fulfilled = outcomes.filter((outcome) => outcome.status === "fulfilled");
  const rejected = outcomes.filter((outcome) => outcome.status === "rejected");

  assert.equal(fulfilled.length, 1);
  assert.equal(rejected.length, 1);
  assert.match(
    String((rejected[0] as PromiseRejectedResult).reason),
    /purchase already quoted with different constraints/,
  );
});

test("paid inference cannot reach a provider before the payment gate authorizes it", async () => {
  const { engine } = await fixture();
  const application = new SellerApplication(engine, new RejectAllPayments());
  await assert.rejects(
    application.paidInference({
      purchaseId: "purchase-1",
      quoteId: "quote-1",
      prompt: "hello",
    }),
    /payment required/,
  );
});

test("paid inference uses the model bound by the authorized quote", async () => {
  const { engine } = await fixture();
  const application = new SellerApplication(engine, {
    async authorize(proof) {
      return { ...proof, modelId: "gemini-large", settlementContext: {} };
    },
    async settle() {
      return { success: true, transaction: `0x${"ab".repeat(32)}`, network: "eip155:84532" };
    },
  });
  const result = await application.paidInference({
    purchaseId: "purchase-1",
    quoteId: "quote-1",
    prompt: "hello",
    paymentSignature: "proof",
  });
  assert.ok(result.result);
  assert.equal(result.result.modelId, "gemini-large");
  assert.equal(result.settlement.success, true);
});

test("paid inference rejects a payment authorization bound to another quote", async () => {
  const { engine } = await fixture();
  const application = new SellerApplication(engine, {
    async authorize() {
      return {
        purchaseId: "purchase-1",
        quoteId: "quote-other",
        modelId: "gemini-fast",
        settlementContext: {},
      };
    },
    async settle() {
      throw new Error("must not settle a mismatched authorization");
    },
  });
  await assert.rejects(
    application.paidInference({
      purchaseId: "purchase-1", quoteId: "quote-1", prompt: "hello",
      paymentSignature: "proof",
    }),
    /authorization binding mismatch/,
  );
});

test("concurrent payment replay settles and invokes the provider exactly once", async () => {
  class CountingProvider extends MockProviderAdapter {
    calls = 0;
    override async generate(modelId: string, prompt: string) {
      this.calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return super.generate(modelId, prompt);
    }
  }
  const provider = new CountingProvider("gemini");
  const { engine } = await fixture({}, provider);
  let settlements = 0;
  const application = new SellerApplication(engine, {
    async authorize(proof) {
      return { ...proof, modelId: "gemini-fast", settlementContext: {} };
    },
    async settle() {
      settlements += 1;
      return {
        success: true,
        transaction: `0x${"ab".repeat(32)}`,
        network: "eip155:84532",
      };
    },
  });
  const args = {
    purchaseId: "purchase-1",
    quoteId: "quote-1",
    prompt: "hello",
    paymentSignature: "proof",
  };
  const [first, second] = await Promise.all([
    application.paidInference(args),
    application.paidInference(args),
  ]);
  assert.strictEqual(second, first);
  assert.equal(settlements, 1);
  assert.equal(provider.calls, 1);
});

test("failed settlement never invokes the provider", async () => {
  const provider = new class extends MockProviderAdapter {
    calls = 0;
    override async generate(modelId: string, prompt: string) {
      this.calls += 1;
      return super.generate(modelId, prompt);
    }
  }("gemini");
  const { engine } = await fixture({}, provider);
  const application = new SellerApplication(engine, {
    async authorize(proof) {
      return { ...proof, modelId: "gemini-fast", settlementContext: {} };
    },
    async settle() {
      return { success: false, transaction: `0x${"cd".repeat(32)}`, network: "eip155:84532" };
    },
  });
  const paid = await application.paidInference({
    purchaseId: "purchase-1",
    quoteId: "quote-1",
    prompt: "hello",
    paymentSignature: "proof",
  });
  assert.equal(paid.result, undefined);
  assert.equal(provider.calls, 0);
});

test("payment replay cannot change the prompt", async () => {
  const { engine } = await fixture();
  const application = new SellerApplication(engine, {
    async authorize(proof) {
      return { ...proof, modelId: "gemini-fast", settlementContext: {} };
    },
    async settle() {
      return { success: true, transaction: `0x${"ab".repeat(32)}`, network: "eip155:84532" };
    },
  });
  await application.paidInference({
    purchaseId: "purchase-1", quoteId: "quote-1", prompt: "first",
    paymentSignature: "proof",
  });
  await assert.rejects(
    application.paidInference({
      purchaseId: "purchase-1", quoteId: "quote-1", prompt: "changed",
      paymentSignature: "proof",
    }),
    /changed the inference binding/,
  );
});

test("cached paid result still requires the exact payment proof", async () => {
  const { engine } = await fixture();
  const application = new SellerApplication(engine, {
    async authorize(proof) {
      return { ...proof, modelId: "gemini-fast", settlementContext: {} };
    },
    async settle() {
      return { success: true, transaction: `0x${"ab".repeat(32)}`, network: "eip155:84532" };
    },
  });
  await application.paidInference({
    purchaseId: "purchase-1",
    quoteId: "quote-1",
    prompt: "hello",
    paymentSignature: "proof-a",
  });
  await assert.rejects(
    application.paidInference({
      purchaseId: "purchase-1", quoteId: "quote-1", prompt: "hello",
    }),
    /payment required/,
  );
  await assert.rejects(
    application.paidInference({
      purchaseId: "purchase-1",
      quoteId: "quote-1",
      prompt: "hello",
      paymentSignature: "proof-b",
    }),
    /changed the inference binding/,
  );
});

test("restart resumes a submitted facilitator settlement and replays the durable result", async () => {
  class MemoryExecutionStore implements SellerExecutionStore {
    record?: SellerExecutionRecord;
    failSettlementRecordOnce = true;
    async claim(args: Parameters<SellerExecutionStore["claim"]>[0]) {
      this.record ??= { ...args, state: "CLAIMED" };
      return this.record;
    }
    async get() { return this.record ?? null; }
    async recordAuthorization(
      _purchaseId: string,
      authorization: NonNullable<SellerExecutionRecord["authorization"]>,
    ) {
      assert.ok(this.record);
      this.record = { ...this.record, state: "SUBMITTED", authorization };
      return this.record;
    }
    async recordSettlement(
      _purchaseId: string,
      settlement: NonNullable<SellerExecutionRecord["settlement"]>,
    ) {
      if (this.failSettlementRecordOnce) {
        this.failSettlementRecordOnce = false;
        throw new Error("injected persistence outage after settle");
      }
      assert.ok(this.record);
      this.record = { ...this.record, state: "SETTLED", settlement };
      return this.record;
    }
    async recordProviderSubmission(
      _purchaseId: string,
      providerAttemptId: string,
      providerAttemptToken: string,
    ) {
      assert.ok(this.record);
      this.record = {
        ...this.record,
        state: "PROVIDER_SUBMITTED",
        providerAttemptId,
        providerAttemptToken,
      };
      return this.record;
    }
    async recordResult(
      _purchaseId: string,
      result: NonNullable<SellerExecutionRecord["result"]>,
    ) {
      assert.ok(this.record);
      this.record = { ...this.record, state: "DELIVERED", result };
      return this.record;
    }
  }
  const provider = new class extends MockProviderAdapter {
    calls = 0;
    override async generate(modelId: string, prompt: string) {
      this.calls += 1;
      return super.generate(modelId, prompt);
    }
  }("gemini");
  const { engine } = await fixture({}, provider);
  const store = new MemoryExecutionStore();
  let settleCalls = 0;
  const payments = {
    async authorize(proof: { purchaseId: string; quoteId: string }) {
      return { ...proof, modelId: "gemini-fast", settlementContext: { replay: true } };
    },
    async settle() {
      settleCalls += 1;
      return { success: true, transaction: `0x${"ab".repeat(32)}`, network: "eip155:84532" };
    },
  };
  const first = new SellerApplication(engine, payments, store);
  await assert.rejects(
    first.paidInference({
      purchaseId: "purchase-1",
      quoteId: "quote-1",
      prompt: "hello",
      paymentSignature: "proof",
    }),
    /persistence outage/,
  );
  assert.equal(store.record?.state, "SUBMITTED");
  const restarted = new SellerApplication(engine, payments, store);
  const recovered = await restarted.recover("purchase-1", "quote-1");
  assert.equal(recovered.result?.text, "mock:hello");
  assert.equal(settleCalls, 2);
  assert.equal(provider.calls, 1);
  const replayed = await new SellerApplication(engine, payments, store).recover(
    "purchase-1",
    "quote-1",
  );
  assert.equal(replayed.result?.responseId, recovered.result?.responseId);
  assert.equal(provider.calls, 1);
});

test("provider result persistence loss never invokes a paid provider twice after restart", async () => {
  class FailingResultStore implements SellerExecutionStore {
    record?: SellerExecutionRecord;
    failResultRecordOnce = true;
    async claim(args: Parameters<SellerExecutionStore["claim"]>[0]) {
      this.record ??= { ...args, state: "CLAIMED" };
      return this.record;
    }
    async get() { return this.record ?? null; }
    async recordAuthorization(
      _purchaseId: string,
      authorization: NonNullable<SellerExecutionRecord["authorization"]>,
    ) {
      assert.ok(this.record);
      this.record = { ...this.record, state: "SUBMITTED", authorization };
      return this.record;
    }
    async recordSettlement(
      _purchaseId: string,
      settlement: NonNullable<SellerExecutionRecord["settlement"]>,
    ) {
      assert.ok(this.record);
      this.record = { ...this.record, state: "SETTLED", settlement };
      return this.record;
    }
    async recordProviderSubmission(
      _purchaseId: string,
      providerAttemptId: string,
      providerAttemptToken: string,
    ) {
      assert.ok(this.record);
      this.record = {
        ...this.record,
        state: "PROVIDER_SUBMITTED",
        providerAttemptId,
        providerAttemptToken,
      };
      return this.record;
    }
    async recordResult(
      _purchaseId: string,
      result: NonNullable<SellerExecutionRecord["result"]>,
    ) {
      if (this.failResultRecordOnce) {
        this.failResultRecordOnce = false;
        throw new Error("injected result persistence outage");
      }
      assert.ok(this.record);
      this.record = { ...this.record, state: "DELIVERED", result };
      return this.record;
    }
  }
  const provider = new class extends MockProviderAdapter {
    calls = 0;
    override async generate(modelId: string, prompt: string) {
      this.calls += 1;
      return super.generate(modelId, prompt);
    }
  }("gemini");
  const { engine } = await fixture({}, provider);
  const store = new FailingResultStore();
  const payments = {
    async authorize(proof: { purchaseId: string; quoteId: string }) {
      return { ...proof, modelId: "gemini-fast", settlementContext: {} };
    },
    async settle() {
      return { success: true, transaction: `0x${"ab".repeat(32)}`, network: "eip155:84532" };
    },
  };
  const first = await new SellerApplication(engine, payments, store).paidInference({
    purchaseId: "purchase-provider-outcome",
    quoteId: "quote-provider-outcome",
    prompt: "hello",
    paymentSignature: "proof",
  });
  assert.match(first.providerError ?? "", /delivery outcome requires reconciliation/);
  assert.equal(store.record?.state, "PROVIDER_SUBMITTED");
  assert.equal(provider.calls, 1);

  const recovered = await new SellerApplication(engine, payments, store).recover(
    "purchase-provider-outcome",
    "quote-provider-outcome",
  );
  assert.match(recovered.providerError ?? "", /delivery outcome requires reconciliation/);
  assert.equal(provider.calls, 1);
});

test("concurrent recovery lets only the provider-attempt CAS winner invoke the provider", async () => {
  class ConcurrentRecoveryStore implements SellerExecutionStore {
    record: SellerExecutionRecord = {
      purchaseId: "purchase-concurrent-recovery",
      quoteId: "quote-concurrent-recovery",
      sellerAgentId: "gemini-agent-8004",
      prompt: "hello",
      promptHash: `sha256:${"11".repeat(32)}`,
      paymentProofHash: `sha256:${"22".repeat(32)}`,
      state: "SETTLED",
      authorization: {
        purchaseId: "purchase-concurrent-recovery",
        quoteId: "quote-concurrent-recovery",
        modelId: "gemini-fast",
        settlementContext: {},
      },
      settlement: {
        success: true,
        transaction: `0x${"ab".repeat(32)}`,
        network: "eip155:84532",
      },
    };
    #reads = 0;
    #releaseReads!: () => void;
    readonly #bothRead = new Promise<void>((resolve) => {
      this.#releaseReads = resolve;
    });

    async claim() { return this.#snapshot(); }
    async get() {
      const snapshot = this.#snapshot();
      this.#reads += 1;
      if (this.#reads === 2) this.#releaseReads();
      await this.#bothRead;
      return snapshot;
    }
    async recordAuthorization(): Promise<never> {
      throw new Error("unexpected authorization");
    }
    async recordSettlement(): Promise<never> {
      throw new Error("unexpected settlement");
    }
    async recordProviderSubmission(
      _purchaseId: string,
      providerAttemptId: string,
      providerAttemptToken: string,
    ) {
      if (this.record.state === "SETTLED") {
        this.record = {
          ...this.record,
          state: "PROVIDER_SUBMITTED",
          providerAttemptId,
          providerAttemptToken,
        };
      }
      return this.#snapshot();
    }
    async recordResult(
      _purchaseId: string,
      result: NonNullable<SellerExecutionRecord["result"]>,
    ) {
      this.record = { ...this.record, state: "DELIVERED", result };
      return this.#snapshot();
    }
    #snapshot(): SellerExecutionRecord {
      return {
        ...this.record,
        ...(this.record.authorization === undefined
          ? {}
          : { authorization: { ...this.record.authorization } }),
        ...(this.record.settlement === undefined
          ? {}
          : { settlement: { ...this.record.settlement } }),
        ...(this.record.result === undefined ? {} : { result: { ...this.record.result } }),
      };
    }
  }

  const provider = new class extends MockProviderAdapter {
    calls = 0;
    override async generate(modelId: string, prompt: string) {
      this.calls += 1;
      return super.generate(modelId, prompt);
    }
  }("gemini");
  const { engine } = await fixture({}, provider);
  const store = new ConcurrentRecoveryStore();
  const payments = {
    async authorize(): Promise<never> { throw new Error("unexpected authorization"); },
    async settle(): Promise<never> { throw new Error("unexpected settlement"); },
  };
  const first = new SellerApplication(engine, payments, store);
  const second = new SellerApplication(engine, payments, store);
  const results = await Promise.all([
    first.recover("purchase-concurrent-recovery", "quote-concurrent-recovery"),
    second.recover("purchase-concurrent-recovery", "quote-concurrent-recovery"),
  ]);
  assert.equal(provider.calls, 1);
  assert.equal(store.record.state, "DELIVERED");
  assert.equal(results.filter((item) => item.result !== undefined).length >= 1, true);
});
