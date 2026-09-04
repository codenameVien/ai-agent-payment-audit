import assert from "node:assert/strict";
import test from "node:test";

import type { Address, Hex } from "viem";

import {
  CommerceGateway,
  type ConfirmedMismatchProof,
  type DecisionSigner,
  type Erc3009Authorization,
  type Erc3009DecisionAuthorization,
  type Erc3009Signer,
  type EvidenceApi,
  type NoTransferProof,
  type PaymentIntent,
  type PaymentRequired,
  type PaymentView,
  type ReceiptProof,
  type ReconciliationCheck,
  type SellerClient,
  type SellerResponse,
  type StagedDelivery,
  type TerminalPaymentEvidenceApi,
  assertTransactionRef,
  encodeHeader,
  evmTransactionRef,
  isTerminalEvidenceApi,
  localTransactionRef,
  TransactionRefError,
} from "../src/index.js";

const BUYER = "0x0000000000000000000000000000000000000002" as Address;
const TOKEN = "0x0000000000000000000000000000000000000003" as Address;
const SELLER = "0x0000000000000000000000000000000000000004" as Address;
const CONTRACT = "0x0000000000000000000000000000000000000005" as Address;
const OTHER_TOKEN = "0x0000000000000000000000000000000000000006" as Address;
const TX = `0x${"ab".repeat(32)}` as Hex;
const STORED_DECISION_HASH = `sha256:${"11".repeat(32)}` as const;
const RUN_ID = "a".repeat(32);
const LOCAL_TX_ID = `localtx:${RUN_ID}:payment:000001`;

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
    reconciliation_attempt_count: 0,
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
    confirmations: 4,
    transfers: [
      { token: TOKEN, from: BUYER, to: SELLER, amount: 100_000n, logIndex: 3 },
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

class FakeTerminalEvidence implements EvidenceApi, TerminalPaymentEvidenceApi {
  readonly calls: string[] = [];
  readonly checks: ReconciliationCheck[] = [];
  mismatch?: ConfirmedMismatchProof;
  noTransfer?: NoTransferProof;
  state: PaymentIntent;
  staged?: StagedDelivery;
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
      transaction_hash: transactionHash ?? this.state.transaction_hash,
    };
    return this.state;
  }
  async bindReconciliationTransaction(
    _purchaseId: string,
    transactionHash: Hex,
  ): Promise<PaymentIntent> {
    this.calls.push("bind-reconciliation-transaction");
    this.state = { ...this.state, transaction_hash: transactionHash };
    return this.state;
  }
  async settle(args: { transactionHash: Hex }): Promise<PaymentIntent> {
    this.calls.push("settle");
    this.state = { ...this.state, state: "SETTLED", transaction_hash: args.transactionHash };
    return this.state;
  }
  async failConfirmed(): Promise<PaymentIntent> {
    this.calls.push("fail");
    this.state = { ...this.state, state: "FAILED" };
    return this.state;
  }
  async recordDelivery(): Promise<void> {
    this.calls.push("delivery");
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
  async recordReconciliationCheck(check: ReconciliationCheck): Promise<PaymentIntent> {
    this.calls.push(`check:${check.verifierOutcome}:${check.attemptNumber}`);
    if (this.checks.some((item) => item.attemptNumber === check.attemptNumber)) {
      throw new Error("duplicate reconciliation attempt");
    }
    this.checks.push(check);
    this.state = {
      ...this.state,
      reconciliation_attempt_count: check.attemptNumber,
      reconciliation_first_checked_at:
        this.state.reconciliation_first_checked_at ?? check.checkedAt,
      reconciliation_last_checked_at: check.checkedAt,
      reconciliation_last_outcome: check.verifierOutcome,
    };
    return this.state;
  }
  async confirmMismatch(proof: ConfirmedMismatchProof): Promise<PaymentIntent> {
    this.calls.push("confirm-mismatch");
    this.mismatch = proof;
    this.state = {
      ...this.state,
      state: "MISMATCH_CONFIRMED",
      actual_transfer: proof.actualTransfer,
      terminal_proof_ref: proof.proofRef,
      terminal_evidence_source: proof.evidenceSource,
      local_transaction_id:
        proof.transactionRef.kind === "LOCAL" ? proof.transactionRef.id : null,
    };
    return this.state;
  }
  async reconcileNoTransfer(proof: NoTransferProof): Promise<PaymentIntent> {
    this.calls.push("reconcile-no-transfer");
    this.noTransfer = proof;
    this.state = {
      ...this.state,
      state: "RECONCILED_NO_TRANSFER",
      no_transfer_reason_code: proof.reasonCode,
      terminal_proof_ref: proof.proofRef,
    };
    return this.state;
  }
}

class FakeSeller implements SellerClient {
  calls = 0;
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
  ) {}
  async request(args: { paymentSignature?: string }): Promise<SellerResponse> {
    this.calls += 1;
    if (args.paymentSignature === undefined) {
      return { status: 402, paymentRequired: encodeHeader(this.required) };
    }
    return this.paidResponse;
  }
  async recover(): Promise<SellerResponse> {
    return { status: 409 };
  }
}

class StubDecisionSigner implements DecisionSigner {
  async signErc3009(_message: Erc3009DecisionAuthorization) {
    return { hash: `0x${"22".repeat(32)}` as Hex, signature: "0xdecision" as Hex };
  }
}

class StubErc3009Signer implements Erc3009Signer {
  async sign(_args: { authorization: Erc3009Authorization }): Promise<Hex> {
    return "0xauthorization";
  }
}

function harness(args?: {
  receipt?: ReceiptProof | null;
  boundedReconciliationAttempts?: number;
}) {
  const { view, intent, required, receipt } = fixtures();
  const evidence = new FakeTerminalEvidence(view, intent);
  const seller = new FakeSeller(required);
  const gateway = new CommerceGateway({
    evidence,
    seller,
    decisionSigner: new StubDecisionSigner(),
    erc3009Signer: new StubErc3009Signer(),
    receipts: {
      async read() {
        return args?.receipt === undefined ? receipt : args.receipt;
      },
    },
    identityVerifier: { async verifyQuoteSigner() {} },
    clock: { nowSeconds() { return 1_800_000_000n; } },
    ...(args?.boundedReconciliationAttempts === undefined
      ? {}
      : { boundedReconciliationAttempts: args.boundedReconciliationAttempts }),
  });
  return { gateway, evidence, seller };
}

test("EVM transaction references reject local identifiers and non-canonical hashes", () => {
  assert.throws(() => evmTransactionRef({ hash: LOCAL_TX_ID }), TransactionRefError);
  assert.throws(() => evmTransactionRef({ hash: `0x${"AB".repeat(32)}` }), TransactionRefError);
  assert.throws(() => evmTransactionRef({ hash: `0x${"ab".repeat(31)}` }), TransactionRefError);
  assert.throws(
    () => evmTransactionRef({ hash: TX, chainId: 1 }),
    /must be on Base Sepolia/,
  );
  assert.deepEqual(evmTransactionRef({ hash: TX }), {
    kind: "EVM",
    hash: TX,
    chainId: 84532,
    evidenceSource: "BASE_SEPOLIA_VERIFIED",
  });
});

test("local transaction references reject EVM hashes and foreign runs", () => {
  assert.throws(() => localTransactionRef({ id: TX, runId: RUN_ID }), TransactionRefError);
  assert.throws(
    () => localTransactionRef({ id: `localtx:${RUN_ID}:payment:1`, runId: RUN_ID }),
    /localtx/,
  );
  assert.throws(
    () => localTransactionRef({ id: LOCAL_TX_ID, runId: "b".repeat(32) }),
    /does not belong/,
  );
  assert.deepEqual(localTransactionRef({ id: LOCAL_TX_ID, runId: RUN_ID }), {
    kind: "LOCAL",
    id: LOCAL_TX_ID,
    runId: RUN_ID,
    evidenceSource: "SYNTHETIC_LOCAL",
  });
});

test("a payload carrying both transaction fields is rejected", () => {
  assert.throws(
    () => assertTransactionRef({ kind: "EVM", hash: TX, id: LOCAL_TX_ID }),
    /both transactionHash and localTransactionId/,
  );
  assert.throws(
    () => assertTransactionRef({ kind: "EVM", hash: TX, evidenceSource: "SYNTHETIC_LOCAL" }),
    /synthetic evidence cannot use an EVM/,
  );
  assert.throws(
    () =>
      assertTransactionRef({
        kind: "LOCAL",
        id: LOCAL_TX_ID,
        runId: RUN_ID,
        evidenceSource: "BASE_SEPOLIA_VERIFIED",
      }),
    /always SYNTHETIC_LOCAL/,
  );
  assert.throws(() => assertTransactionRef({ kind: "OTHER" }), /kind must be EVM or LOCAL/);
  assert.throws(() => assertTransactionRef(null), /malformed/);
});

test("the terminal capability guard requires all three operations", () => {
  const { view, intent } = fixtures();
  assert.equal(isTerminalEvidenceApi(new FakeTerminalEvidence(view, intent)), true);
  assert.equal(isTerminalEvidenceApi({ confirmMismatch() {} }), false);
  assert.equal(isTerminalEvidenceApi(null), false);
});

test("an exact settlement records no reconciliation attempt", async () => {
  const { gateway, evidence } = harness();
  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "SETTLED");
  assert.equal(evidence.checks.length, 0);
  assert.ok(evidence.calls.includes("settle"));
  assert.ok(evidence.mismatch === undefined);
  assert.ok(evidence.noTransfer === undefined);
});

for (const [label, mutate, expectedFields] of [
  [
    "wrong amount",
    (receipt: ReceiptProof) => {
      receipt.transfers[0]!.amount = 200_000n;
    },
    { amountUnits: 200_000, token: TOKEN, to: SELLER },
  ],
  [
    "wrong token",
    (receipt: ReceiptProof) => {
      receipt.transfers[0]!.token = OTHER_TOKEN;
    },
    { amountUnits: 100_000, token: OTHER_TOKEN, to: SELLER },
  ],
  [
    "wrong recipient",
    (receipt: ReceiptProof) => {
      receipt.transfers[0]!.to = CONTRACT;
    },
    { amountUnits: 100_000, token: TOKEN, to: CONTRACT },
  ],
] as const) {
  test(`a confirmed ${label} outflow becomes a terminal mismatch proof`, async () => {
    const { receipt } = fixtures();
    mutate(receipt);
    const { gateway, evidence } = harness({ receipt });

    const result = await gateway.execute("purchase-1");

    assert.equal(result.state, "MISMATCH_CONFIRMED");
    assert.equal(evidence.checks.length, 1);
    assert.equal(evidence.checks[0]!.verifierOutcome, "MISMATCHED_TRANSFER_CONFIRMED");
    assert.equal(evidence.mismatch?.actualTransfer.amountUnits, expectedFields.amountUnits);
    assert.equal(evidence.mismatch?.actualTransfer.token, expectedFields.token);
    assert.equal(evidence.mismatch?.actualTransfer.to, expectedFields.to);
    assert.equal(evidence.mismatch?.actualTransfer.from, BUYER);
    assert.equal(evidence.mismatch?.transactionRef.kind, "EVM");
    assert.ok(!evidence.calls.includes("settle"));
    assert.ok(evidence.noTransfer === undefined);
  });
}

test("a synthetic receipt keeps its local reference out of the EVM hash field", async () => {
  const { receipt } = fixtures();
  receipt.transfers[0]!.amount = 200_000n;
  receipt.transactionRef = localTransactionRef({ id: LOCAL_TX_ID, runId: RUN_ID });
  const { gateway, evidence } = harness({ receipt });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "MISMATCH_CONFIRMED");
  assert.equal(evidence.mismatch?.evidenceSource, "SYNTHETIC_LOCAL");
  assert.equal(evidence.mismatch?.transactionRef.kind, "LOCAL");
  assert.equal(
    evidence.mismatch?.transactionRef.kind === "LOCAL"
      ? evidence.mismatch.transactionRef.id
      : undefined,
    LOCAL_TX_ID,
  );
  assert.equal(evidence.checks[0]!.evidenceSource, "SYNTHETIC_LOCAL");
  assert.equal(evidence.checks[0]!.submissionRef, LOCAL_TX_ID);
  assert.ok(!JSON.stringify(evidence.mismatch).includes(TX));
});

test("a success receipt without any buyer outflow stays unknown until the bound is reached", async () => {
  const { receipt } = fixtures();
  receipt.transfers = [];
  receipt.authorizations = [];
  const { gateway, evidence } = harness({ receipt, boundedReconciliationAttempts: 3 });

  const first = await gateway.execute("purchase-1");
  assert.equal(first.state, "RECONCILIATION_REQUIRED");
  assert.ok(evidence.noTransfer === undefined, "first attempt must not close the payment");

  const second = await gateway.reconcile("purchase-1", TX);
  assert.equal(second.state, "RECONCILIATION_REQUIRED");
  assert.ok(evidence.noTransfer === undefined, "second attempt must not close the payment");

  const third = await gateway.reconcile("purchase-1", TX);
  const closed = evidence.noTransfer as NoTransferProof | undefined;
  assert.equal(third.state, "RECONCILED_NO_TRANSFER");
  assert.equal(evidence.checks.length, 3);
  assert.deepEqual(
    evidence.checks.map((check) => check.attemptNumber),
    [1, 2, 3],
  );
  assert.equal(closed?.reasonCode, "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER");
  assert.equal(closed?.attemptCount, 3);
  assert.equal(closed?.finalityEvidence.confirmations, 4);
  assert.match(closed?.authorizationNonceHash ?? "", /^sha256:[0-9a-f]{64}$/);
});

test("a receipt with unknown finality still reports zero confirmations", async () => {
  const { receipt } = fixtures();
  receipt.transfers = [];
  delete receipt.confirmations;
  const { gateway, evidence } = harness({ receipt, boundedReconciliationAttempts: 1 });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "RECONCILED_NO_TRANSFER");
  assert.equal(evidence.noTransfer?.finalityEvidence.confirmations, 0);
});

test("a missing receipt records a transient attempt and never closes the payment", async () => {
  const { gateway, evidence } = harness({ receipt: null, boundedReconciliationAttempts: 1 });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.equal(evidence.checks.length, 1);
  assert.equal(evidence.checks[0]!.verifierOutcome, "RECEIPT_NOT_FOUND");
  assert.ok(evidence.noTransfer === undefined);
  assert.ok(evidence.mismatch === undefined);
  assert.match(evidence.calls.at(-1) ?? "", /^reconciliation:receipt pending/);
});

test("a reverted receipt records the attempt and then fails confirmed", async () => {
  const { receipt } = fixtures();
  receipt.status = 0;
  receipt.transfers = [];
  const { gateway, evidence } = harness({ receipt });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "FAILED");
  assert.equal(evidence.checks.at(-1)?.verifierOutcome, "RECEIPT_REVERTED");
  assert.equal(evidence.calls.at(-1), "fail");
  assert.ok(evidence.noTransfer === undefined);
});

test("more than one buyer outflow is ambiguous and never terminal", async () => {
  const { receipt } = fixtures();
  receipt.transfers = [
    { token: TOKEN, from: BUYER, to: SELLER, amount: 60_000n, logIndex: 1 },
    { token: TOKEN, from: BUYER, to: CONTRACT, amount: 40_000n, logIndex: 2 },
  ];
  const { gateway, evidence } = harness({ receipt, boundedReconciliationAttempts: 1 });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.equal(evidence.checks.at(-1)?.verifierOutcome, "AMBIGUOUS_TRANSFER_EVIDENCE");
  assert.ok(evidence.mismatch === undefined);
  assert.ok(evidence.noTransfer === undefined);
});

test("an exact transfer without a matching authorization stays unknown", async () => {
  const { receipt } = fixtures();
  receipt.authorizations = [];
  const { gateway, evidence } = harness({ receipt, boundedReconciliationAttempts: 1 });

  const result = await gateway.execute("purchase-1");

  assert.equal(result.state, "RECONCILIATION_REQUIRED");
  assert.equal(evidence.checks.at(-1)?.verifierOutcome, "AMBIGUOUS_TRANSFER_EVIDENCE");
  assert.equal(evidence.checks.at(-1)?.authorizationState, "MISSING_OR_AMBIGUOUS");
  assert.ok(!evidence.calls.includes("settle"));
});

test("reconciliation attempts are consecutive and carry canonical proof references", async () => {
  const { receipt } = fixtures();
  receipt.transfers = [];
  const { gateway, evidence } = harness({ receipt, boundedReconciliationAttempts: 10 });

  await gateway.execute("purchase-1");
  await gateway.reconcile("purchase-1", TX);

  assert.deepEqual(
    evidence.checks.map((check) => check.attemptNumber),
    [1, 2],
  );
  for (const check of evidence.checks) {
    assert.match(check.proofRef, /^sha256:[0-9a-f]{64}$/);
    assert.equal(check.submissionRef, TX);
    assert.equal(check.checkedChainId, 84532);
    assert.equal(check.purchaseId, "purchase-1");
  }
});

test("a legacy Permit2 intent never reaches terminal classification", async () => {
  const { view, intent } = fixtures();
  const evidence = new FakeTerminalEvidence(view, {
    ...intent,
    transfer_method: "permit2",
    authorization_nonce: null,
    permit2_nonce: "123456789",
  });
  const gateway = new CommerceGateway({
    evidence,
    seller: new FakeSeller(fixtures().required),
    decisionSigner: new StubDecisionSigner(),
    erc3009Signer: new StubErc3009Signer(),
    receipts: { async read() { return fixtures().receipt; } },
    identityVerifier: { async verifyQuoteSigner() {} },
    clock: { nowSeconds() { return 1_800_000_000n; } },
  });

  await assert.rejects(() => gateway.execute("purchase-1"), /read-only/);
  assert.equal(evidence.checks.length, 0);
  assert.ok(evidence.mismatch === undefined);
  assert.ok(evidence.noTransfer === undefined);
});
