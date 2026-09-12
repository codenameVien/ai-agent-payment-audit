// Opt-in real local Qwen + temporary MongoDB + Mock payment E2E. No wallet keys.
import assert from "node:assert/strict";
import { startAegisStack } from "./aegis_local_stack.mjs";

const stack = await startAegisStack({ priorityClassifier: "local-qwen" });
async function api(path, body) {
  const response = await fetch(`${stack.evidenceUrl}${path}`, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const value = await response.json();
  assert.ok(response.ok, JSON.stringify(value));
  return value;
}
try {
  for (const provider of ["openai", "anthropic", "google"]) {
    const created = await api("/purchases", {
      domain: "ai_inference", budget_units: 50000, policy: {},
      request: { requestSchema: "aegis-aa-v1", prompt: "비용을 최소화해서 짧게 요약해줘.", allowed_providers: [provider] },
    });
    const id = created.purchase_id;
    const run = await api(`/purchases/${id}/run`, {});
    assert.equal(run.payment.state, "SETTLED");
    assert.equal(run.payment.execution_mode, "mock");
    assert.equal(run.payment.provider_id, provider);
    const events = await api(`/purchases/${id}/events`);
    const decision = events.find(e => e.type === "DECIDED").payload;
    assert.equal(decision.token.symbol, "AEGIS");
    assert.equal(decision.priority.effectivePriority, "price");
    assert.equal(decision.priority.classificationMethod, "ollama-qwen-structured-v1");
    assert.ok(events.some(e => e.type === "AUDITED"));
    await api(`/purchases/${id}/run`, {});
    const after = await api(`/purchases/${id}/events`);
    assert.equal(after.filter(e => e.type === "PAYMENT_SETTLED").length, 1);
    console.log(JSON.stringify({ provider, purchaseId: id, priority: decision.priority,
      token: decision.token.symbol, payment: "mock", audited: true, duplicateSettlements: 0 }));
  }
} finally { await stack.stop(); }
