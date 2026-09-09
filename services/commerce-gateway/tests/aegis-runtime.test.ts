/**
 * Provider Gateway and payment execution module over real HTTP.
 *
 * Every hop below is an actual request: buyer -> gateway -> facilitator -> evidence API.
 * Only the Evidence API and the chain are doubles, and the Evidence API double answers
 * the same wire contract the FastAPI service does.
 */

import assert from "node:assert/strict";
import test from "node:test";

import type { Hex } from "viem";

import { AegisEvidenceClient } from "../src/aegis/evidence-client.js";
import { MockFacilitator } from "../src/aegis/facilitator.js";
import { AegisPaymentExecutor } from "../src/aegis/payment-executor.js";
import { ProviderGateway } from "../src/aegis/provider-gateway.js";
import { createMockProvider, mockProviderFor, type MockProvider } from "../src/aegis/providers.js";
import { AegisAuthorizationSigner } from "../src/aegis/signer.js";
import { AegisTermsError } from "../src/aegis/terms.js";
import { X402BindingError } from "../src/aegis/x402.js";
import {
  buildDecisionEvidence,
  startEvidenceApiDouble,
  startHandlerServer,
  TEST_MODELS,
  TEST_NETWORK,
  type EvidenceApiDouble,
} from "./aegis-fixtures.js";

const INTERNAL_TOKEN = "test-internal-token";
const SIGNER_KEY = `0x${"33".repeat(32)}` as Hex;
const AMOUNTS: Record<string, bigint> = { openai: 619n, anthropic: 4116n, google: 2568n };

type Wrapper = (
  handler: (request: Request) => Promise<Response>,
) => (request: Request) => Promise<Response>;

interface Harness {
  evidence: EvidenceApiDouble;
  executor: AegisPaymentExecutor;
  buyerAddress: string;
  routes: Record<string, string>;
  close(): Promise<void>;
}

async function startHarness(
  options: { providers?: Record<string, MockProvider>; wrap?: Record<string, Wrapper> } = {},
): Promise<Harness> {
  const signer = new AegisAuthorizationSigner(SIGNER_KEY, 84532);
  const evidence = await startEvidenceApiDouble({
    internalServiceToken: INTERNAL_TOKEN,
    buyerWalletAddress: signer.address.toLowerCase(),
  });
  const facilitator = new MockFacilitator();
  const facilitatorServer = await startHandlerServer((request) => facilitator.handle(request));
  const closers: (() => Promise<void>)[] = [facilitatorServer.close, evidence.close];
  const routes: Record<string, string> = {};
  for (const providerId of Object.keys(TEST_MODELS)) {
    const gateway = new ProviderGateway({
      providerId,
      provider: options.providers?.[providerId] ?? mockProviderFor(providerId),
      evidence: new AegisEvidenceClient({
        baseUrl: evidence.url,
        internalServiceToken: INTERNAL_TOKEN,
      }),
      facilitatorUrl: facilitatorServer.url,
      network: TEST_NETWORK,
    });
    const base = (request: Request): Promise<Response> => gateway.handle(request);
    const wrapper = options.wrap?.[providerId];
    const server = await startHandlerServer(wrapper === undefined ? base : wrapper(base));
    routes[providerId] = server.url;
    closers.push(server.close);
  }
  return {
    evidence,
    buyerAddress: signer.address.toLowerCase(),
    routes,
    executor: new AegisPaymentExecutor({
      evidence: new AegisEvidenceClient({
        baseUrl: evidence.url,
        internalServiceToken: INTERNAL_TOKEN,
      }),
      signer,
      gatewayRoutes: routes,
      network: TEST_NETWORK,
      gatewayTimeoutMs: 1_500,
    }),
    close: async () => {
      for (const close of closers) await close();
    },
  };
}

function record(harness: Harness, purchaseId: string, providerId: string): void {
  harness.evidence.purchases.set(
    purchaseId,
    buildDecisionEvidence({
      purchaseId,
      model: TEST_MODELS[providerId]!,
      amountUnits: AMOUNTS[providerId]!,
    }),
  );
}

/** Reserve and authorize an attempt the way the executor does, without paying it. */
async function reserveAndAuthorize(
  harness: Harness,
  purchaseId: string,
  authorizationHash: string,
): Promise<void> {
  const headers = {
    "content-type": "application/json",
    authorization: `Bearer ${INTERNAL_TOKEN}`,
  };
  await fetch(`${harness.evidence.url}/internal/evidence/aegis/payments/reserve`, {
    method: "POST",
    headers,
    body: JSON.stringify({ purchase_id: purchaseId }),
  });
  await fetch(`${harness.evidence.url}/internal/evidence/payment-intents/authorize`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      purchase_id: purchaseId,
      authorization_hash: authorizationHash,
      signature: `0x${"cd".repeat(65)}`,
    }),
  });
}

for (const providerId of Object.keys(TEST_MODELS)) {
  test(`${providerId}: 402 challenge, ERC-3009 signature, settlement, mock result`, async () => {
    const harness = await startHarness();
    try {
      const purchaseId = `purchase-${providerId}`;
      record(harness, purchaseId, providerId);
      const result = await harness.executor.execute({
        purchaseId,
        resourceBody: { prompt: "통합 검증용 프롬프트" },
      });
      assert.equal(result.payment_status, "settled");
      assert.equal(result.state, "SETTLED");
      assert.equal(result.provider_model_id, TEST_MODELS[providerId]!.providerModelId);
      assert.equal(result.amount_units, Number(AMOUNTS[providerId]!));
      assert.equal(result.execution_mode, "mock");
      assert.equal(result.verification_basis, "facilitator_response");
      assert.ok(result.settlement_reference?.startsWith("x402mock:"));
      assert.equal(harness.evidence.settlements.length, 1);
      assert.equal(harness.evidence.deliveries.length, 1);
      assert.equal(harness.evidence.failures.length, 0);
    } finally {
      await harness.close();
    }
  });
}

test("a gateway refuses a purchase that selected another provider", async () => {
  const harness = await startHarness();
  try {
    const purchaseId = "purchase-misrouted";
    record(harness, purchaseId, "anthropic");
    // Ask the OpenAI gateway to serve a purchase that decided on Anthropic.
    const response = await fetch(
      `${harness.routes.openai}/v1/inference?purchaseId=${purchaseId}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: "오라우팅 검증" }),
      },
    );
    assert.equal(response.status, 409);
    assert.match(String((await response.json()).error), /selected anthropic/);
    assert.equal(harness.evidence.settlements.length, 0);
  } finally {
    await harness.close();
  }
});

test("a tampered 402 is refused before anything is signed", async () => {
  const cheapened: Wrapper = (handler) => async (request) => {
    const response = await handler(request);
    const header = response.headers.get("PAYMENT-REQUIRED");
    if (header === null) return response;
    const challenge = JSON.parse(Buffer.from(header, "base64").toString("utf8")) as {
      accepts: { amount: string }[];
    };
    challenge.accepts[0]!.amount = "1";
    const headers = new Headers(response.headers);
    headers.set(
      "PAYMENT-REQUIRED",
      Buffer.from(JSON.stringify(challenge), "utf8").toString("base64"),
    );
    return new Response(await response.text(), { status: response.status, headers });
  };
  const harness = await startHarness({ wrap: { openai: cheapened } });
  try {
    const purchaseId = "purchase-tampered-402";
    record(harness, purchaseId, "openai");
    await assert.rejects(
      harness.executor.execute({ purchaseId, resourceBody: { prompt: "변조 검증" } }),
      X402BindingError,
    );
    assert.equal(harness.evidence.settlements.length, 0);
    // The reservation exists, but nothing was authorized against altered terms.
    assert.equal(harness.evidence.intents.get(purchaseId)?.state, "CLAIMED");
  } finally {
    await harness.close();
  }
});

test("a gateway refuses a payment payload that is not the decided terms", async () => {
  const harness = await startHarness();
  try {
    const purchaseId = "purchase-underpaid";
    record(harness, purchaseId, "openai");
    await reserveAndAuthorize(harness, purchaseId, `0x${"ee".repeat(32)}`);
    const forged = Buffer.from(
      JSON.stringify({
        x402Version: 2,
        resource: { url: "http://127.0.0.1/v1/inference", description: "", mimeType: "" },
        accepted: {
          scheme: "exact",
          network: TEST_NETWORK,
          // A client that decides for itself what the purchase costs.
          amount: "1",
          asset: "0x0000000000000000000000000000000000000000",
          payTo: TEST_MODELS.openai!.recipient,
          maxTimeoutSeconds: 60,
          extra: { assetTransferMethod: "eip3009", name: "PBL Agent Credit", version: "2" },
        },
        payload: {
          signature: `0x${"ab".repeat(65)}`,
          authorization: {
            from: harness.buyerAddress,
            to: TEST_MODELS.openai!.recipient,
            value: "1",
            validAfter: "0",
            validBefore: "99999999999",
            nonce: `0x${"a7".repeat(32)}`,
          },
        },
        extensions: { aegis: {} },
      }),
      "utf8",
    ).toString("base64");
    const response = await fetch(
      `${harness.routes.openai}/v1/inference?purchaseId=${purchaseId}`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "PAYMENT-SIGNATURE": forged },
        body: JSON.stringify({ prompt: "과소 지불 검증" }),
      },
    );
    assert.equal(response.status, 402);
    assert.equal(response.headers.get("PAYMENT-RESPONSE"), null);
    assert.match(String((await response.json()).error), /does not match the decided terms/);
    assert.equal(harness.evidence.settlements.length, 0);
  } finally {
    await harness.close();
  }
});

test("an unanswered settlement is parked for reconciliation, never retried", async () => {
  const stalled: Wrapper = (handler) => async (request) => {
    if (request.headers.get("PAYMENT-SIGNATURE") === null) return handler(request);
    await new Promise((done) => setTimeout(done, 5_000));
    return handler(request);
  };
  const harness = await startHarness({ wrap: { openai: stalled } });
  try {
    const purchaseId = "purchase-timeout";
    record(harness, purchaseId, "openai");
    const result = await harness.executor.execute({
      purchaseId,
      resourceBody: { prompt: "타임아웃 검증" },
    });
    assert.equal(result.payment_status, "unknown");
    assert.equal(result.state, "RECONCILIATION_REQUIRED");
    assert.equal(harness.evidence.reconciliations.length, 1);
    assert.equal(harness.evidence.settlements.length, 0);
    assert.equal(harness.evidence.failures.length, 0);

    // A second attempt on an ambiguous purchase must not create a fresh payment.
    const again = await harness.executor.execute({
      purchaseId,
      resourceBody: { prompt: "타임아웃 검증" },
    });
    assert.equal(again.payment_status, "unknown");
    assert.equal(harness.evidence.settlements.length, 0);
  } finally {
    await harness.close();
  }
});

test("a provider failure after settlement never pays again", async () => {
  const failing = createMockProvider({
    providerId: "openai",
    providerModelId: TEST_MODELS.openai!.providerModelId,
    modelVersion: TEST_MODELS.openai!.modelVersion,
    vendorLabel: "OpenAI",
  });
  const brokenProvider: MockProvider = {
    providerId: failing.providerId,
    providerModelId: failing.providerModelId,
    modelVersion: failing.modelVersion,
    complete: async () => {
      throw new Error("mock provider is unavailable");
    },
  };
  const harness = await startHarness({ providers: { openai: brokenProvider } });
  try {
    const purchaseId = "purchase-delivery-failure";
    record(harness, purchaseId, "openai");
    const result = await harness.executor.execute({
      purchaseId,
      resourceBody: { prompt: "전달 실패 검증" },
    });
    assert.equal(result.payment_status, "settled_delivery_failed");
    assert.equal(result.provider_error, "mock provider is unavailable");
    assert.equal(harness.evidence.settlements.length, 1);
    assert.equal(harness.evidence.deliveries.length, 0);

    const retried = await harness.executor.execute({
      purchaseId,
      resourceBody: { prompt: "전달 실패 검증" },
    });
    assert.equal(retried.payment_status, "settled_delivery_failed");
    // Still exactly one settlement: the gateway short-circuits an already paid purchase.
    assert.equal(harness.evidence.settlements.length, 1);
  } finally {
    await harness.close();
  }
});

test("a terminal purchase is never paid again", async () => {
  const harness = await startHarness();
  try {
    const purchaseId = "purchase-terminal";
    record(harness, purchaseId, "openai");
    await harness.executor.execute({ purchaseId, resourceBody: { prompt: "준비" } });
    harness.evidence.intents.get(purchaseId)!.state = "FAILED";
    const result = await harness.executor.execute({ purchaseId, resourceBody: { prompt: "재시도" } });
    assert.equal(result.payment_status, "already_terminal");
    assert.equal(harness.evidence.settlements.length, 1);
  } finally {
    await harness.close();
  }
});

test("a unit count that lost precision on the wire is refused", async () => {
  const harness = await startHarness();
  try {
    const purchaseId = "purchase-lossy";
    record(harness, purchaseId, "openai");
    harness.evidence.budgetUnits = 2 ** 53;
    await assert.rejects(
      harness.executor.execute({ purchaseId, resourceBody: { prompt: "정밀도 검증" } }),
      AegisTermsError,
    );
    assert.equal(harness.evidence.settlements.length, 0);
  } finally {
    await harness.close();
  }
});
