#!/usr/bin/env node
// 실제 프로세스로 기동한 aa-three-factor-v1 기본 구성의 E2E 스모크.
//
// 증거 API(실제 MongoDB) + Mock Provider Gateway 3개 + Mock Facilitator + 결제 실행 모듈을
// 모두 별도 프로세스로 올리고 HTTP 로만 통신한다. 실제 AA/Provider/체인 호출은 없고,
// 임시 mongod 와 임시 키만 사용하며 사용자의 기존 DB 는 건드리지 않는다.

import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import { startAegisStack } from "./aegis_local_stack.mjs";

const PROVIDERS = {
  openai: { providerModelId: "gpt-4.1-mini-2025-04-14", modelVersion: "2025-04-14" },
  anthropic: { providerModelId: "claude-haiku-4-5-20251001", modelVersion: "2025-10-01" },
  google: { providerModelId: "gemini-2.5-flash", modelVersion: "gemini-2.5-flash" },
};

let stack;

before(async () => {
  stack = await startAegisStack();
}, { timeout: 180_000 });

after(async () => {
  if (stack !== undefined) await stack.stop();
});

async function api(path, init = {}) {
  const response = await fetch(`${stack.evidenceUrl}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
  });
  const text = await response.text();
  return { status: response.status, body: text.length === 0 ? undefined : JSON.parse(text) };
}

async function createPurchase(providerId, prompt) {
  const created = await api("/purchases", {
    method: "POST",
    body: JSON.stringify({
      domain: "ai_inference",
      budget_units: 50_000,
      policy: { scoringPolicyVersion: "aa-three-factor-v1" },
      request: {
        requestSchema: "aegis-aa-v1",
        prompt,
        allowed_providers: [providerId],
      },
    }),
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  return created.body.purchase_id;
}

for (const [providerId, expected] of Object.entries(PROVIDERS)) {
  test(`${providerId}: request -> 402 -> settle -> mock result`, { timeout: 60_000 }, async () => {
    const purchaseId = await createPurchase(providerId, `${providerId} 공급자 모의 요청입니다.`);
    const run = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(run.status, 200, JSON.stringify(run.body));

    const payment = run.body.payment;
    assert.equal(payment.payment_status, "settled");
    assert.equal(payment.state, "SETTLED");
    assert.equal(payment.provider_id, providerId);
    assert.equal(payment.provider_model_id, expected.providerModelId);
    assert.equal(payment.model_version, expected.modelVersion);
    assert.equal(payment.execution_mode, "mock");
    assert.equal(payment.verification_basis, "facilitator_response");
    assert.equal(payment.aa_mode, "fixture");
    assert.equal(payment.aa_mapping_provenance, "fixture");
    assert.equal(payment.settlement_network, "eip155:84532");
    // A Facilitator answer this runtime never verified on chain must not look like one.
    assert.ok(payment.settlement_reference.startsWith("x402mock:"), payment.settlement_reference);
    assert.ok(!payment.settlement_reference.startsWith("0x"));
    assert.equal(payment.provider_result.executionMode, "mock");
    assert.equal(payment.provider_result.providerModelId, expected.providerModelId);

    const events = await api(`/purchases/${purchaseId}/events`);
    assert.equal(events.status, 200);
    const types = events.body.map((event) => event.type);
    assert.deepEqual(types, [
      "REQUESTED",
      "AA_SNAPSHOT_RECORDED",
      "DECIDED",
      "PAYMENT_INTENT_CLAIMED",
      "PAYMENT_AUTHORIZED",
      "PAYMENT_SETTLED",
      "DELIVERED",
      "AUDITED",
    ]);
    const settled = events.body.find((event) => event.type === "PAYMENT_SETTLED");
    assert.equal(settled.payload.verificationBasis, "facilitator_response");
    assert.equal(settled.payload.executionMode, "mock");
    assert.equal(settled.payload.facilitatorNetwork, "eip155:84532");
    assert.equal(settled.payload.transactionHash, undefined);

    // No key, no signature and no prompt text ever reaches the stored evidence.
    const serialized = JSON.stringify(events.body);
    assert.ok(!serialized.includes(stack.buyerWalletAddress.slice(2).toLowerCase() + "deadbeef"));
    assert.ok(!/"signature"/.test(serialized));
    assert.ok(!serialized.includes("공급자 모의 요청"));
  });
}

test("a duplicate concurrent run settles exactly once", { timeout: 60_000 }, async () => {
  const purchaseId = await createPurchase("openai", "동시 중복 요청 검증용 프롬프트입니다.");
  const runs = await Promise.all([
    api(`/purchases/${purchaseId}/run`, { method: "POST" }),
    api(`/purchases/${purchaseId}/run`, { method: "POST" }),
  ]);
  assert.ok(runs.some((run) => run.status === 200), JSON.stringify(runs));

  const events = await api(`/purchases/${purchaseId}/events`);
  const settlements = events.body.filter((event) => event.type === "PAYMENT_SETTLED");
  const claims = events.body.filter((event) => event.type === "PAYMENT_INTENT_CLAIMED");
  assert.equal(settlements.length, 1);
  assert.equal(claims.length, 1);
});

test("re-running a settled purchase delivers without paying again", { timeout: 60_000 }, async () => {
  const purchaseId = await createPurchase("anthropic", "결제 완료 후 재실행 검증용 프롬프트입니다.");
  const first = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
  assert.equal(first.status, 200, JSON.stringify(first.body));
  assert.equal(first.body.payment.payment_status, "settled");

  const again = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
  assert.equal(again.status, 200, JSON.stringify(again.body));
  assert.equal(again.body.payment.payment_status, "settled");
  assert.equal(
    again.body.payment.provider_result.responseId,
    first.body.payment.provider_result.responseId,
  );
  // The durable settle count, read from the evidence itself, is still one.
  const events = await api(`/purchases/${purchaseId}/events`);
  const settlements = events.body.filter((event) => event.type === "PAYMENT_SETTLED");
  const deliveries = events.body.filter((event) => event.type === "DELIVERED");
  assert.equal(settlements.length, 1);
  assert.equal(deliveries.length, 1);
  assert.equal(
    events.body.filter((event) => event.type === "PAYMENT_AUTHORIZED").length,
    1,
  );
});

test("internal routes refuse an unauthenticated caller", async () => {
  const purchaseId = await createPurchase("google", "내부 경계 검증용 프롬프트입니다.");
  for (const path of [
    `/internal/purchases/${purchaseId}/aegis-decision`,
    `/internal/evidence/aegis/purchases/${purchaseId}/payment-terms`,
  ]) {
    assert.equal((await api(path)).status, 401);
  }
  const reserve = await api("/internal/evidence/aegis/payments/reserve", {
    method: "POST",
    body: JSON.stringify({ purchase_id: purchaseId }),
  });
  assert.equal(reserve.status, 401);
});

test("the superseded Phase 6 endpoints are not part of this composition", async () => {
  const purchaseId = await createPurchase("anthropic", "레거시 경계 검증용 프롬프트입니다.");
  const legacy = await api(`/internal/evidence/purchases/${purchaseId}/payment-view`, {
    headers: { authorization: `Bearer ${stack.internalServiceToken}` },
  });
  assert.equal(legacy.status, 404);
  const siwe = await api("/auth/siwe/challenge", {
    method: "POST",
    body: JSON.stringify({ owner_address: stack.buyerWalletAddress }),
  });
  assert.equal(siwe.status, 404);
});

test("a cross-site browser POST cannot drive the local owner", async () => {
  const response = await fetch(`${stack.evidenceUrl}/purchases`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      origin: "http://evil.example",
      "sec-fetch-site": "cross-site",
    },
    body: JSON.stringify({ domain: "ai_inference", request: {}, budget_units: 1 }),
  });
  assert.equal(response.status, 403);
});
