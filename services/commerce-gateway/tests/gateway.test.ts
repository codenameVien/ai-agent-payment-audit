import assert from "node:assert/strict";
import test from "node:test";

import type { Address, Hex } from "viem";

import {
  CommerceGateway,
  type DecisionSigner,
  type Erc3009Authorization,
  type Erc3009DecisionAuthorization,
  type Erc3009Signer,
  type EvidenceApi,
  type PaymentIntent,
  type PaymentPayload,
  type PaymentRequired,
  type PaymentView,
  type ReceiptProof,
  type SellerClient,
  type SellerResponse,
  type StagedDelivery,
  X402BindingError,
  encodeHeader,
} from "../src/index.js";

const BUYER = "0x0000000000000000000000000000000000000002" as Address;
const TOKEN = "0x0000000000000000000000000000000000000003" as Address;
const SELLER = "0x0000000000000000000000000000000000000004" as Address;
const CONTRACT = "0x0000000000000000000000000000000000000005" as Address;
const TX = `0x${"ab".repeat(32)}` as Hex;
const DECISION_HASH = `0x${"11".repeat(32)}` as Hex;
const STORED_DECISION_HASH = `sha256:${"11".repeat(32)}` as const;

function fixtures() {
  const view: PaymentView = {
    purchase_id: "purchase-1",
    owner_address: "0x0000000000000000000000000000000000000001",
    buyer_wallet_address: BUYER,
    budget_units: 250_000,
    request_policy: {},
    decision_event_hash: STORED_DECISION_HASH,
    quote: {
      quote_id: "quote-1",
      seller_agent_id: "seller-agent-1",
      erc8004_agent_id: "1",
      provider_id: "gemini",
      model_id: "model-a",
      model_version: "v1",
      amount_units: 100_000,
      token: TOKEN,
      pay_to: SELLER,
      expires_at: "2027-01-15T08:20:00.000Z",
      signer_address: SELLER,
      chain_id: 84532,
      verifying_contract: CONTRACT,
    },
    event_count: 3,
    head_event_hash: STORED_DECISION_HASH,
  };
  const intent: PaymentIntent = {
    purchase_id: view.purchase_id,
    buyer_wallet_address: BUYER,
    policy_date: "2026-09-02",
    quote_id: view.quote.quote_id,
    decision_event_hash: STORED_DECISION_HASH,
    amount_units: view.quote.amount_units,
    token: TOKEN,
    pay_to: SELLER,
    transfer_method: "eip3009",
    authorization_nonce: `0x${"44".repeat(32)}`,
    state: "CLAIMED",
    claimed_at: "2026-09-02T00:00:00Z",
  };
  const required: PaymentRequired = {
    x402Version: 2,
    resource: {
      url: "https://seller.example/v1/inference",
      description: "one inference",
      mimeType: "application/json",
    },
    accepts: [
      {
        scheme: "exact",
        network: "eip155:84532",
        amount: String(intent.amount_units),
        asset: TOKEN,
        payTo: SELLER,
        maxTimeoutSeconds: 60,
        extra: {
          assetTransferMethod: "eip3009",
          name: "PBL Agent Credit",
          version: "2",
        },
      },
    ],
    extensions: {},
  };
  const receipt: ReceiptProof = {
    transactionHash: TX,
    status: 1,
    blockNumber: 42,
    transfers: [
      {
        token: TOKEN,
        from: BUYER,
        to: SELLER,
        amount: 100_000n,
        logIndex: 3,
      },
    ],
    authorizations: [
      {
        token: TOKEN,
        authorizer: BUYER,
        nonce: intent.authorization_nonce!,
        logIndex: 2,
      },
    ],
  };
  return { view, intent, required, receipt };
}

class FakeEvidence implements EvidenceApi {
  readonly calls: string[] = [];
  state: PaymentIntent;
  staged?: StagedDelivery;
  failDeliveryOnce = false;
  constructor(readonly view: PaymentView, intent: PaymentIntent) {
    this.state = { ...intent };
  }
  async paymentView(): Promise<PaymentView> {
    this.calls.push("view");
    return this.view;
  }
  async claim(): Promise<PaymentIntent> {
    this.calls.push("claim");
    return this.state;
  }
  async authorize(args: { authorizationHash: Hex; signature: Hex }): Promise<PaymentIntent> {
    this.calls.push("authorize");
    this.state = {
      ...this.state,
      state: "AUTHORIZED",
      decision_authorization_hash: args.authorizationHash,
      decision_authorization_signature: args.signature,
    };
    return this.state;
  }
  async reconciliation(
    _purchaseId: string,
    reason: string,
    transactionHash?: Hex,
  ): Promise<PaymentIntent> {
    this.calls.push(`reconciliation:${reason}`);
    this.state = {
      ...this.state,
      state: "RECONCILIATION_REQUIRED",
      transaction_hash: transactionHash,
    };
    return this.state;
  }
  async bindReconciliationTransaction(
    _purchaseId: string,
    transactionHash: Hex,
  ): Promise<PaymentIntent> {
    this.calls.push("bind-reconciliation-transaction");
    if (
      this.state.state !== "RECONCILIATION_REQUIRED" ||
      (this.state.transaction_hash != null &&
        this.state.transaction_hash.toLowerCase() !== transactionHash.toLowerCase())
    ) throw new Error("payment already has a different transaction");
    this.state = { ...this.state, transaction_hash: transactionHash };
    return this.state;
  }
  async settle(args: { transactionHash: Hex }): Promise<PaymentIntent> {
    this.calls.push("settle");
    this.state = {
      ...this.state,
      state: "SETTLED",
      transaction_hash: args.transactionHash,
    };
    return this.state;
  }
  async failConfirmed(): Promise<PaymentIntent> {
    this.calls.push("fail");
    this.state = { ...this.state, state: "FAILED" };
    return this.state;
  }
  async recordDelivery(): Promise<void> {
    this.calls.push("delivery");
    if (this.failDeliveryOnce) {
      this.failDeliveryOnce = false;
      throw new Error("injected delivery persistence crash");
    }
  }
  async stageDelivery(args: {
    sellerAgentId: string;
    providerId: string;
    responseId: string;
    modelId: string;
    modelVersion: string;
    text: string;
  }): Promise<StagedDelivery> {
    this.calls.push("stage-delivery");
    this.staged = {
      seller_agent_id: args.sellerAgentId,
      provider_id: args.providerId,
      response_id: args.responseId,
      response_hash: "sha256:response",
      model_id: args.modelId,
      model_version: args.modelVersion,
      text: args.text,
    };
    return this.staged;
  }
  async stagedDelivery(): Promise<StagedDelivery | null> {
    this.calls.push("staged-delivery");
    return this.staged ?? null;
  }
}

class CapturingDecisionSigner implements DecisionSigner {
  message?: Erc3009DecisionAuthorization;
  async signErc3009(message: Erc3009DecisionAuthorization) {
    this.message = message;
    return {
      hash: `0x${"22".repeat(32)}` as Hex,
      signature: "0xdecision" as Hex,
    };
  }
}

class CapturingErc3009Signer implements Erc3009Signer {
  authorization?: Erc3009Authorization;
  async sign(args: { authorization: Erc3009Authorization }): Promise<Hex> {
    this.authorization = args.authorization;
    return "0xauthorization";
  }
}

class FakeSeller implements SellerClient {
  paymentPayload?: PaymentPayload;
  calls = 0;
  recoveryCalls = 0;
  constructor(
    readonly required: PaymentRequired,
    readonly paidResponse: SellerResponse = {
      status: 200,
      paymentResponse: encodeHeader({
        success: true,
        transaction: TX,
        network: "eip155:84532",
        payer: BUYER,
      }),
      body: {
        providerId: "gemini",
        responseId: "response-1",
        modelId: "model-a",
        modelVersion: "v1",
        text: "answer",
      },
    },
    readonly recoveryResponse: SellerResponse = { status: 409 },
  ) {}
  async request(args: { paymentSignature?: string }): Promise<SellerResponse> {
    this.calls += 1;
    if (args.paymentSignature === undefined) {
      return { status: 402, paymentRequired: encodeHeader(this.required) };
    }
    this.paymentPayload = JSON.parse(
      Buffer.from(args.paymentSignature, "base64").toString("utf8"),
    ) as PaymentPayload;
    return this.paidResponse;
  }
  async recover(): Promise<SellerResponse> {
    this.recoveryCalls += 1;
    return this.recoveryResponse;
  }
}

function harness(args?: {
  mutateRequirement?: (required: PaymentRequired) => void;
  paidResponse?: SellerResponse;
  recoveryResponse?: SellerResponse;
  receipt?: ReceiptProof | null;
  mutateIntent?: (intent: PaymentIntent) => void;
  erc3009Signer?: Erc3009Signer;
  identityVerifier?: { verifyQuoteSigner(agentId: bigint, signer: Address): Promise<unknown> };
}) {
  const { view, intent, required, receipt } = fixtures();
  args?.mutateRequirement?.(required);
  args?.mutateIntent?.(intent);
  const evidence = new FakeEvidence(view, intent);
  const seller = new FakeSeller(required, args?.paidResponse, args?.recoveryResponse);
  const decisionSigner = new CapturingDecisionSigner();
  const erc3009Signer = new CapturingErc3009Signer();
  const gateway = new CommerceGateway({
    evidence,
    seller,
    decisionSigner,
    erc3009Signer: args?.erc3009Signer ?? erc3009Signer,
    receipts: { async read() { return args?.receipt === undefined ? receipt : args.receipt; } },
    identityVerifier: args?.identityVerifier ?? {
      async verifyQuoteSigner(agentId, signer) {
        assert.equal(agentId, 1n);
        assert.equal(signer, SELLER);
      },
    },
    clock: { nowSeconds() { return 1_800_000_000n; } },
  });
  return { gateway, evidence, seller, decisionSigner, erc3009Signer };
}

test("one purchase binds decision, x402 ERC-3009 payload and exact Transfer", async () => {
  const { gateway, evidence, seller, decisionSigner, erc3009Signer } = harness();
  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "SETTLED");
  assert.deepEqual(evidence.calls, [
    "view",
    "claim",
    "authorize",
    "stage-delivery",
    "reconciliation:facilitator returned submitted transaction",
    "settle",
    "delivery",
  ]);
  assert.equal(seller.calls, 2);
  assert.equal(decisionSigner.message?.decisionEventHash, DECISION_HASH);
  assert.equal(decisionSigner.message?.authorizationNonce, `0x${"44".repeat(32)}`);
  assert.equal(erc3009Signer.authorization?.to, SELLER);
  assert.equal(erc3009Signer.authorization?.nonce, `0x${"44".repeat(32)}`);
  assert.equal(seller.paymentPayload?.x402Version, 2);
  assert.equal(seller.paymentPayload?.payload.authorization.value, "100000");
});

for (const [label, mutate] of [
  ["amount", (required: PaymentRequired) => { required.accepts[0]!.amount = "99999"; }],
  ["token", (required: PaymentRequired) => { required.accepts[0]!.asset = CONTRACT; }],
  ["recipient", (required: PaymentRequired) => { required.accepts[0]!.payTo = CONTRACT; }],
  ["network", (required: PaymentRequired) => { required.accepts[0]!.network = "eip155:1"; }],
  ["transfer method", (required: PaymentRequired) => {
    required.accepts[0]!.extra = { assetTransferMethod: "permit2" };
  }],
] as const) {
  test(`substituted 402 ${label} is rejected before signing`, async () => {
    const { gateway, evidence, seller } = harness({ mutateRequirement: mutate });
    await assert.rejects(() => gateway.execute("purchase-1"), X402BindingError);
    assert.deepEqual(evidence.calls, ["view", "claim"]);
    assert.equal(seller.calls, 1);
  });
}

test("missing receipt enters reconciliation without a second payment", async () => {
  const { gateway, evidence, seller } = harness({ receipt: null });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.equal(seller.calls, 2);
  assert.equal(evidence.calls.filter((item) => item === "claim").length, 1);
  assert.match(evidence.calls.at(-1) ?? "", /^reconciliation:receipt pending/);
});

test("wrong Transfer never settles and remains reserved for reconciliation", async () => {
  const { receipt } = fixtures();
  receipt.transfers[0]!.to = CONTRACT;
  const { gateway, evidence } = harness({ receipt });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.ok(!evidence.calls.includes("settle"));
});

test("failed facilitator response persists the transaction before inspecting provider body", async () => {
  const { receipt } = fixtures();
  receipt.status = 0;
  receipt.transfers = [];
  const { gateway, evidence } = harness({
    receipt,
    paidResponse: {
      status: 402,
      paymentResponse: encodeHeader({
        success: false,
        transaction: TX,
        network: "eip155:84532",
        payer: BUYER,
        errorReason: "execution_reverted",
      }),
      body: { error: "settlement failed" },
    },
  });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "FAILED");
  assert.ok(evidence.calls.includes("reconciliation:execution_reverted"));
  assert.equal(evidence.calls.at(-1), "fail");
  assert.ok(!evidence.calls.includes("stage-delivery"));
});

test("seller cannot substitute the selected model after payment", async () => {
  const { gateway, evidence } = harness({
    paidResponse: {
      status: 200,
      paymentResponse: encodeHeader({
        success: true,
        transaction: TX,
        network: "eip155:84532",
        payer: BUYER,
      }),
      body: {
        providerId: "gemini",
        responseId: "response-1",
        modelId: "substituted-model",
        modelVersion: "v1",
        text: "answer",
      },
    },
  });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "SETTLED");
  assert.ok(evidence.calls.includes("reconciliation:seller delivery changed selected model binding"));
  assert.ok(!evidence.calls.includes("stage-delivery"));
  assert.ok(evidence.calls.includes("settle"));
  assert.ok(!evidence.calls.includes("delivery"));
});

test("independently confirmed revert releases through the failure transition", async () => {
  const { receipt } = fixtures();
  receipt.status = 0;
  receipt.transfers = [];
  const { gateway, evidence } = harness({ receipt });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "FAILED");
  assert.equal(evidence.calls.at(-1), "fail");
});

test("decision and claimed intent mismatch stops before contacting seller", async () => {
  const { gateway, evidence, seller } = harness({
    mutateIntent(intent) { intent.amount_units += 1; },
  });
  await assert.rejects(
    () => gateway.execute("purchase-1"),
    /changed decision binding/,
  );
  assert.deepEqual(evidence.calls, ["view", "claim"]);
  assert.equal(seller.calls, 0);
});

test("legacy Permit2 intent remains readable but cannot execute", async () => {
  const { gateway, evidence, seller } = harness({
    mutateIntent(intent) {
      intent.transfer_method = "permit2";
      intent.authorization_nonce = null;
      intent.permit2_nonce = "123456789";
    },
  });
  await assert.rejects(() => gateway.execute("purchase-1"), /read-only/);
  assert.deepEqual(evidence.calls, ["view", "claim"]);
  assert.equal(seller.calls, 0);
});

test("post-authorization missing PAYMENT-RESPONSE never resubmits", async () => {
  const { gateway, evidence, seller } = harness({
    paidResponse: { status: 504 },
  });
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.equal(seller.calls, 2);
  assert.equal(evidence.calls.filter((item) => item === "authorize").length, 1);
  const resumed = await gateway.execute("purchase-1");
  assert.equal(resumed.state, "RECONCILIATION_REQUIRED");
  assert.equal(seller.calls, 2);
});

test("hashless reconciliation recovers a durable seller settlement without resubmitting", async () => {
  const { gateway, evidence, seller } = harness({
    paidResponse: { status: 504 },
    recoveryResponse: {
      status: 200,
      paymentResponse: encodeHeader({
        success: true,
        transaction: TX,
        network: "eip155:84532",
        payer: BUYER,
      }),
      body: {
        providerId: "gemini",
        responseId: "response-recovered",
        modelId: "model-a",
        modelVersion: "v1",
        text: "recovered answer",
      },
    },
  });
  const pending = await gateway.execute("purchase-1");
  assert.equal(pending.state, "RECONCILIATION_REQUIRED");
  assert.equal(pending.transaction_hash, undefined);
  const recovered = await gateway.execute("purchase-1");
  assert.equal(recovered.state, "SETTLED");
  assert.equal(seller.calls, 2);
  assert.equal(seller.recoveryCalls, 1);
  assert.ok(evidence.calls.includes("bind-reconciliation-transaction"));
  assert.ok(evidence.calls.includes("stage-delivery"));
  assert.ok(evidence.calls.includes("settle"));
  assert.ok(evidence.calls.includes("delivery"));
});

test("manual reconciliation rejects an unrelated reverted transaction hash", async () => {
  const { gateway, evidence } = harness();
  await assert.rejects(
    () => gateway.reconcile("purchase-1", `0x${"99".repeat(32)}` as Hex),
    /not bound to this payment intent/,
  );
  assert.equal(evidence.state.state, "CLAIMED");
  assert.ok(!evidence.calls.includes("fail"));
});

test("crash after authorization resumes with the same ERC-3009 nonce", async () => {
  class FailOnceErc3009Signer implements Erc3009Signer {
    calls: Erc3009Authorization[] = [];
    async sign(args: { authorization: Erc3009Authorization }): Promise<Hex> {
      this.calls.push(args.authorization);
      if (this.calls.length === 1) throw new Error("injected process crash");
      return "0xauthorization";
    }
  }
  const signer = new FailOnceErc3009Signer();
  const { gateway, evidence, seller } = harness({ erc3009Signer: signer });
  await assert.rejects(() => gateway.execute("purchase-1"), /injected process crash/);
  assert.equal(evidence.state.state, "AUTHORIZED");
  const result = await gateway.execute("purchase-1");
  assert.equal(result.state, "SETTLED");
  assert.equal(evidence.calls.filter((item) => item === "authorize").length, 1);
  assert.equal(signer.calls.length, 2);
  assert.equal(signer.calls[0]?.nonce, signer.calls[1]?.nonce);
  assert.equal(seller.calls, 3);
});

test("ERC-8004 signer mismatch aborts before contacting the seller", async () => {
  const { gateway, evidence, seller } = harness({
    identityVerifier: {
      async verifyQuoteSigner() { throw new Error("quote signer is not registered"); },
    },
  });
  await assert.rejects(() => gateway.execute("purchase-1"), /not registered/);
  assert.deepEqual(evidence.calls, ["view"]);
  assert.equal(seller.calls, 0);
});

test("crash after settlement resumes staged provider delivery without a second payment", async () => {
  const { gateway, evidence, seller } = harness();
  evidence.failDeliveryOnce = true;
  await assert.rejects(() => gateway.execute("purchase-1"), /delivery persistence crash/);
  assert.equal(evidence.state.state, "SETTLED");
  assert.equal(seller.calls, 2);
  const resumed = await gateway.execute("purchase-1");
  assert.equal(resumed.state, "SETTLED");
  assert.deepEqual(resumed.provider_result, {
    providerId: "gemini",
    responseId: "response-1",
    modelId: "model-a",
    modelVersion: "v1",
    text: "answer",
  });
  assert.equal(seller.calls, 2);
  assert.equal(evidence.calls.filter((item) => item === "stage-delivery").length, 1);
  assert.equal(evidence.calls.filter((item) => item === "delivery").length, 2);
});
