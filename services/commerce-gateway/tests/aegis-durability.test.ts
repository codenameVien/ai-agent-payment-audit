/**
 * The durable single-attempt guarantee of one purchase.
 *
 * The reviewer reproduced two successful settlements for one purchase by presenting two
 * different, individually valid ERC-3009 authorizations to fresh gateway instances. These
 * cover that reproduction and the restart paths around it: the authority is the payment
 * intent the Evidence API durably reserved, never a gateway's in-memory cache.
 */

import assert from "node:assert/strict";
import test from "node:test";

import type { Hex } from "viem";

import { AegisEvidenceClient } from "../src/aegis/evidence-client.js";
import { MockFacilitator } from "../src/aegis/facilitator.js";
import { AegisPaymentExecutor } from "../src/aegis/payment-executor.js";
import { ProviderGateway } from "../src/aegis/provider-gateway.js";
import { mockProviderFor } from "../src/aegis/providers.js";
import { AegisAuthorizationSigner } from "../src/aegis/signer.js";
import { deriveTerms } from "../src/aegis/terms.js";
import {
  bindingFor,
  encodeHeader,
  requirementsFor,
  type AegisPaymentPayload,
} from "../src/aegis/x402.js";
import {
  buildDecisionEvidence,
  startEvidenceApiDouble,
  startHandlerServer,
  TEST_MODELS,
  TEST_NETWORK,
  type EvidenceApiDouble,
} from "./aegis-fixtures.js";

const INTERNAL_TOKEN = "test-internal-token";
const SIGNER_KEY = `0x${"44".repeat(32)}` as Hex;
const PURCHASE = "purchase-durable";
const AMOUNT = 619n;

interface Stack {
  evidence: EvidenceApiDouble;
  facilitator: MockFacilitator;
  facilitatorUrl: string;
  signer: AegisAuthorizationSigner;
  /** A brand new gateway process, with an empty in-memory settlement cache. */
  newGateway(): Promise<{ url: string; close(): Promise<void> }>;
  newExecutor(routes: Record<string, string>): AegisPaymentExecutor;
  close(): Promise<void>;
}

async function startStack(options: { facilitator?: MockFacilitator } = {}): Promise<Stack> {
  const signer = new AegisAuthorizationSigner(SIGNER_KEY, 84532);
  const evidence = await startEvidenceApiDouble({
    internalServiceToken: INTERNAL_TOKEN,
    buyerWalletAddress: signer.address.toLowerCase(),
  });
  const facilitator = options.facilitator ?? new MockFacilitator();
  const facilitatorServer = await startHandlerServer((request) => facilitator.handle(request));
  const opened: (() => Promise<void>)[] = [facilitatorServer.close, evidence.close];
  evidence.purchases.set(
    PURCHASE,
    buildDecisionEvidence({ purchaseId: PURCHASE, model: TEST_MODELS.openai!, amountUnits: AMOUNT }),
  );
  return {
    evidence,
    facilitator,
    facilitatorUrl: facilitatorServer.url,
    signer,
    async newGateway() {
      const gateway = new ProviderGateway({
        providerId: "openai",
        provider: mockProviderFor("openai"),
        evidence: new AegisEvidenceClient({
          baseUrl: evidence.url,
          internalServiceToken: INTERNAL_TOKEN,
        }),
        facilitatorUrl: facilitatorServer.url,
        network: TEST_NETWORK,
      });
      const server = await startHandlerServer((request) => gateway.handle(request));
      opened.push(server.close);
      return server;
    },
    newExecutor(routes) {
      return new AegisPaymentExecutor({
        evidence: new AegisEvidenceClient({
          baseUrl: evidence.url,
          internalServiceToken: INTERNAL_TOKEN,
        }),
        signer,
        gatewayRoutes: routes,
        network: TEST_NETWORK,
        gatewayTimeoutMs: 2_000,
      });
    },
    close: async () => {
      for (const close of opened) await close();
    },
  };
}

/** Sign an authorization for this purchase under an arbitrary nonce. */
async function signedPayload(stack: Stack, nonce: Hex): Promise<string> {
  const evidence = stack.evidence.purchases.get(PURCHASE)!;
  const terms = deriveTerms({
    purchaseId: evidence.purchase_id as string,
    decision: evidence.decision as Record<string, unknown>,
    decisionEventHash: evidence.decision_event_hash as string,
    snapshot: evidence.snapshot as Record<string, unknown>,
    snapshotEventHash: evidence.snapshot_event_hash as string,
    snapshotHash: evidence.snapshot_hash as string,
  });
  const requirement = requirementsFor({ terms, network: TEST_NETWORK, maxTimeoutSeconds: 60 });
  const now = Math.floor(Date.now() / 1000);
  const authorization = {
    from: stack.signer.address,
    to: terms.recipient as `0x${string}`,
    value: terms.amountUnits.toString(),
    validAfter: String(now - 10),
    validBefore: String(now + 600),
    nonce,
  };
  const signed = await stack.signer.sign({
    token: terms.token.address as `0x${string}`,
    tokenName: requirement.extra.name,
    tokenVersion: requirement.extra.version,
    authorization,
  });
  const payload: AegisPaymentPayload = {
    x402Version: 2,
    resource: { url: "http://127.0.0.1/v1/inference", description: "", mimeType: "" },
    accepted: requirement,
    payload: { signature: signed.signature, authorization },
    extensions: { aegis: bindingFor(terms, "mock") },
  };
  return encodeHeader(payload);
}

async function pay(url: string, header: string): Promise<Response> {
  return fetch(`${url}/v1/inference?purchaseId=${PURCHASE}`, {
    method: "POST",
    headers: { "content-type": "application/json", "PAYMENT-SIGNATURE": header },
    body: JSON.stringify({ prompt: "내구성 검증용 프롬프트" }),
  });
}

test("two different valid authorizations for one purchase settle at most once", async () => {
  const stack = await startStack();
  try {
    // The reviewer's reproduction: the same purchase, two individually valid nonces, and
    // a fresh gateway instance for each so no in-memory cache can be the guard.
    const first = await stack.newGateway();
    const second = await stack.newGateway();
    const executor = stack.newExecutor({ openai: first.url });
    const paid = await executor.execute({
      purchaseId: PURCHASE,
      resourceBody: { prompt: "내구성 검증용 프롬프트" },
    });
    assert.equal(paid.payment_status, "settled");

    const foreign = await pay(second.url, await signedPayload(stack, `0x${"22".repeat(32)}`));
    // Already settled, so the second gateway delivers the result and never settles again.
    assert.equal(foreign.status, 200);
    assert.equal(foreign.headers.get("PAYMENT-RESPONSE"), null);
    assert.equal(stack.evidence.settlements.length, 1);
  } finally {
    await stack.close();
  }
});

test("a second nonce is refused while the reserved attempt is still awaiting settlement", async () => {
  const stack = await startStack();
  try {
    const gateway = await stack.newGateway();
    // Reserve and authorize the attempt, binding it to the digest of nonce 11...
    const executor = stack.newExecutor({ openai: gateway.url });
    void executor;
    const reserved = await fetch(
      `${stack.evidence.url}/internal/evidence/aegis/payments/reserve`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${INTERNAL_TOKEN}`,
        },
        body: JSON.stringify({ purchase_id: PURCHASE }),
      },
    );
    const intentNonce = (await reserved.json()).intent.authorization_nonce as Hex;
    await fetch(`${stack.evidence.url}/internal/evidence/payment-intents/authorize`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${INTERNAL_TOKEN}`,
      },
      body: JSON.stringify({
        purchase_id: PURCHASE,
        authorization_hash: `0x${"ee".repeat(32)}`,
        signature: `0x${"cd".repeat(65)}`,
      }),
    });

    const other = await pay(gateway.url, await signedPayload(stack, `0x${"22".repeat(32)}`));
    assert.equal(other.status, 402);
    assert.match(String((await other.json()).error), /nonce reserved for this purchase/);

    // Even the reserved nonce is refused while its recorded digest is a different one.
    const reservedNonce = await pay(gateway.url, await signedPayload(stack, intentNonce));
    assert.equal(reservedNonce.status, 402);
    assert.match(String((await reservedNonce.json()).error), /not the one recorded/);
    assert.equal(stack.evidence.settlements.length, 0);
  } finally {
    await stack.close();
  }
});

test("a settled purchase is delivered after a gateway and facilitator restart", async () => {
  const stack = await startStack();
  try {
    const first = await stack.newGateway();
    const paid = await stack
      .newExecutor({ openai: first.url })
      .execute({ purchaseId: PURCHASE, resourceBody: { prompt: "재시작 검증" } });
    assert.equal(paid.payment_status, "settled");
    assert.equal(stack.evidence.settlements.length, 1);
    assert.equal(stack.evidence.deliveries.length, 1);

    // A brand new gateway process and a brand new Facilitator: every in-memory record of
    // the payment is gone, and only the durable evidence remains.
    const restartedFacilitator = new MockFacilitator();
    const restarted = await startHandlerServer((request) => restartedFacilitator.handle(request));
    try {
      const gateway = await stack.newGateway();
      const result = await stack
        .newExecutor({ openai: gateway.url })
        .execute({ purchaseId: PURCHASE, resourceBody: { prompt: "재시작 검증" } });
      assert.equal(result.payment_status, "settled");
      assert.equal(result.provider_result !== null, true);
      // No signature, no verify, no settle: still exactly one settlement.
      assert.equal(stack.evidence.settlements.length, 1);
      assert.equal(stack.evidence.failures.length, 0);
      assert.equal(stack.evidence.reconciliations.length, 0);
      assert.equal(stack.evidence.deliveries.length, 2);
    } finally {
      await restarted.close();
    }
  } finally {
    await stack.close();
  }
});

interface MismatchCase {
  label: string;
  answer: Partial<{ payer: string | undefined; network: string; amount: string }>;
  expected: RegExp;
  /** What the durable record must show, taken from the answer, not from our expectations. */
  recordedPayer: string | null | "signer";
  recordedNetwork: string;
  recordedAmount: string;
}

const MISMATCH_CASES: MismatchCase[] = [
  {
    label: "an unattributed payer",
    answer: { payer: undefined },
    expected: /did not name the payer/,
    recordedPayer: null,
    recordedNetwork: TEST_NETWORK,
    recordedAmount: AMOUNT.toString(),
  },
  {
    label: "a foreign payer",
    answer: { payer: "0x00000000000000000000000000000000000bad01" },
    expected: /payer this module did not authorize/,
    recordedPayer: "0x00000000000000000000000000000000000bad01",
    recordedNetwork: TEST_NETWORK,
    recordedAmount: AMOUNT.toString(),
  },
  {
    label: "another network",
    answer: { network: "eip155:1" },
    expected: /not the decided/,
    recordedPayer: "signer",
    recordedNetwork: "eip155:1",
    recordedAmount: AMOUNT.toString(),
  },
  {
    label: "a different amount",
    answer: { amount: "1" },
    expected: /facilitator settled 1/,
    recordedPayer: "signer",
    recordedNetwork: TEST_NETWORK,
    recordedAmount: "1",
  },
];

for (const { label, answer, expected, recordedPayer, recordedNetwork, recordedAmount } of
  MISMATCH_CASES) {
  test(`a settlement success with ${label} is unresolved, never a failure`, async () => {
    const facilitator = new MockFacilitator();
    const original = facilitator.settle.bind(facilitator);
    // Only the answer is distorted; the authorization and the requirements are untouched,
    // so this is a Facilitator that reports success while contradicting what it answered.
    facilitator.settle = async (context) => ({ ...(await original(context)), ...answer });
    const stack = await startStack({ facilitator });
    try {
      const gateway = await stack.newGateway();
      const executor = stack.newExecutor({ openai: gateway.url });
      const result = await executor.execute({
        purchaseId: PURCHASE,
        resourceBody: { prompt: "응답 대조 검증" },
      });

      // Unresolved, not failed: the money may have moved.
      assert.equal(result.payment_status, "unknown");
      assert.equal(result.state, "RECONCILIATION_REQUIRED");
      assert.match(String(result.provider_error), expected);
      assert.equal(stack.evidence.settlements.length, 0);
      assert.equal(stack.evidence.deliveries.length, 0);
      // The reservation is retained: nothing was released as a confirmed non-payment.
      assert.equal(stack.evidence.failures.length, 0);
      assert.equal(stack.evidence.intents.get(PURCHASE)?.state, "RECONCILIATION_REQUIRED");

      // The original answer is preserved durably, including the fields it omitted.
      assert.equal(stack.evidence.ambiguous.length, 1);
      const recorded = stack.evidence.ambiguous[0]!;
      assert.equal(recorded.success, true);
      assert.equal(recorded.network, recordedNetwork);
      assert.equal(
        recorded.payer,
        recordedPayer === "signer" ? stack.signer.address.toLowerCase() : recordedPayer,
      );
      assert.equal(recorded.amount, recordedAmount);
      assert.match(String(recorded.transaction), /^x402mock:/);
      assert.match(String(recorded.mismatch_reason), expected);

      // A retry neither signs, verifies, settles nor pays again.
      const retried = await executor.execute({
        purchaseId: PURCHASE,
        resourceBody: { prompt: "응답 대조 재시도" },
      });
      assert.equal(retried.payment_status, "unknown");
      assert.equal(stack.evidence.settlements.length, 0);
      assert.equal(stack.evidence.failures.length, 0);
      assert.equal(stack.evidence.ambiguous.length, 1);
    } finally {
      await stack.close();
    }
  });
}

test("a matching settlement keeps the Facilitator's own answer in the record", async () => {
  const stack = await startStack();
  try {
    const gateway = await stack.newGateway();
    const result = await stack
      .newExecutor({ openai: gateway.url })
      .execute({ purchaseId: PURCHASE, resourceBody: { prompt: "정상 응답 보존" } });
    assert.equal(result.payment_status, "settled");
    assert.equal(stack.evidence.settlements.length, 1);
    const recorded = stack.evidence.settlements[0]!;
    // The amount the Facilitator reported is stored, not only the one we expected.
    assert.equal(recorded.facilitator_amount, AMOUNT.toString());
    assert.equal(recorded.facilitator_payer, stack.signer.address.toLowerCase());
    assert.equal(recorded.facilitator_network, TEST_NETWORK);
  } finally {
    await stack.close();
  }
});
