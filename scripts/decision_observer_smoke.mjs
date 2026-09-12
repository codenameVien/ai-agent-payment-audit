// Real local HTTP/Mongo orchestration, fixture AA, Mock token settlement/Anchor.
// --qwen also calls installed loopback Ollama. No real wallet key or broadcast.
import assert from "node:assert/strict";
import { startAegisStack } from "./aegis_local_stack.mjs";
const qwen = process.argv.includes("--qwen");
const stack = await startAegisStack({ observerMode: qwen ? "local-qwen" : "mock", checkpointMode: "mock" });
async function api(path, body) {
  const response = await fetch(`${stack.evidenceUrl}${path}`, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const text = await response.text();
  assert.ok(response.ok, `HTTP ${response.status}: ${text.slice(0,500)}`);
  return JSON.parse(text);
}
try {
  for (const provider of ["openai", "anthropic", "google"]) {
    const created = await api("/purchases", { domain: "ai_inference", budget_units: 50000, policy: {},
      request: { requestSchema: "aegis-aa-v1", prompt: "비용을 최소화해서 한 문장으로 요약해줘.", priority: "price", allowed_providers: [provider] } });
    const id = created.purchase_id;
    const result = await api(`/purchases/${id}/run`, {});
    assert.equal(result.payment.state, "SETTLED");
    const events = await api(`/purchases/${id}/events`);
    const checkpoints = events.filter(e => e.type === "CHECKPOINT_RECORDED");
    assert.equal(checkpoints.length, 2);
    const decision = checkpoints.find(e => e.payload.phase === "decision");
    const audit = checkpoints.find(e => e.payload.phase === "audit");
    const settled = events.find(e => e.type === "PAYMENT_SETTLED");
    const authorized = events.find(e => e.type === "PAYMENT_AUTHORIZED");
    assert.ok(decision.sequence < authorized.sequence);
    assert.ok(audit.sequence > settled.sequence);
    assert.ok(events.find(e => e.type === "AUDITED").sequence < audit.sequence);
    for (const checkpoint of checkpoints) {
      assert.equal(checkpoint.payload.mode, "mock");
      assert.ok(!checkpoint.payload.transactionHash);
      assert.equal(events[checkpoint.payload.eventCount - 1].event_hash, checkpoint.payload.headEventHash);
    }
    const observed = events.filter(e => e.type.startsWith("OBSERVER_"));
    assert.equal(observed.length, 2);
    if (qwen) {
      console.log(JSON.stringify({ observations: observed.map(e => ({ phase: e.payload.phase,
        type: e.type, model: e.payload.model, diagnosticCode: e.payload.diagnosticCode })) }));
      assert.ok(observed.every(e => e.type === "OBSERVER_COMPLETED"), "local Qwen observer incomplete");
    }
    await api(`/purchases/${id}/run`, {});
    const repeated = await api(`/purchases/${id}/events`);
    assert.equal(repeated.filter(e => e.type === "PAYMENT_SETTLED").length, 1);
    assert.equal(repeated.filter(e => e.type === "CHECKPOINT_RECORDED").length, 2);
    assert.equal(repeated.filter(e => e.type.startsWith("OBSERVER_")).length, 2);
    console.log(JSON.stringify({ provider, purchaseId: id, observer: qwen ? "local-qwen" : "mock",
      beforePaymentAnchor: "mock", afterAuditAnchor: "mock", settlementCount: 1, safeReplay: true }));
  }
} finally { await stack.stop(); }
