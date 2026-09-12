import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

// Behaviour of the pending-request resume policy. `src/lib/aegis.ts` is imported
// directly: Node >= 22.18 strips the type annotations.
const stripsTypes = Boolean(process.features.typescript);
const aegis = stripsTypes ? await import("../src/lib/aegis.ts") : null;
const skip = stripsTypes ? false : "requires a Node build with TypeScript type stripping";

const OLD_COMMON_KEY = "pbl:purchase-request-id";
const OLD_EXPERIMENT_KEY = "pbl:normal-experiment-purchase-id";
const NEW_KEY = "aegis:token-v1:purchase-request-id";

/** A store that fails loudly if the resume policy ever writes to browser storage. */
function store(values) {
  return {
    getItem: (key) => (key in values ? values[key] : null),
    setItem: () => assert.fail("pending resolution must not write to storage"),
    removeItem: () => assert.fail("pending resolution must not remove stored keys"),
  };
}

function detail({
  purchaseId = "aegis-purchase",
  schema = "aegis-aa-v1",
  promptHash = "sha256:" + "a".repeat(64),
  originalPriority = null,
  effectivePriority = "price",
  budgetUnits = 750000,
  paymentStatus = "PAYMENT_PENDING",
  audit = null,
} = {}) {
  return {
    summary: {
      purchase_id: purchaseId,
      created_at: "2026-09-09T00:00:00Z",
      domain: "ai_inference",
      request_summary:
        schema === null
          ? { priority: "balanced", required_capabilities: [] }
          : {
              request_schema_version: schema,
              prompt_hash: promptHash,
              original_priority: originalPriority,
              effective_priority: effectivePriority,
            },
      status: "REQUESTED",
      amount_units: null,
      token: null,
      transaction_hash: null,
      audit_severity: null,
      finding_count: 0,
      lifecycle_status: "REQUESTED",
      payment_status: paymentStatus,
      audit_status: "PENDING_AUDIT",
      audit_covers_head: false,
    },
    events: [
      {
        event_id: "REQUESTED-1",
        sequence: 1,
        type: "REQUESTED",
        occurred_at: "2026-09-09T00:00:00Z",
        actor: {},
        payload: { budgetUnits, domain: "ai_inference" },
        event_hash: "sha256:head",
        evidence_refs: [],
        redacted: false,
      },
    ],
    audit,
  };
}

function recordingLoad(result) {
  const calls = [];
  const load = async (purchaseId) => {
    calls.push(purchaseId);
    if (result instanceof Error) throw result;
    return result;
  };
  return { calls, load };
}

test("the new pending key is not one of the superseded runtime keys", { skip }, () => {
  assert.equal(aegis.AEGIS_PENDING_PURCHASE_KEY, NEW_KEY);
  assert.deepEqual(aegis.HISTORICAL_PENDING_PURCHASE_KEYS, [
    OLD_COMMON_KEY,
    OLD_EXPERIMENT_KEY,
  ]);
  assert.ok(!aegis.HISTORICAL_PENDING_PURCHASE_KEYS.includes(aegis.AEGIS_PENDING_PURCHASE_KEY));
});

test("an id left in the old common key is history, never a resume target", { skip }, async () => {
  const { calls, load } = recordingLoad(detail());
  const resolution = await aegis.resolvePendingRequest(
    store({ [OLD_COMMON_KEY]: "legacy-purchase" }),
    load,
  );
  assert.equal(resolution.status, "none");
  assert.equal(resolution.purchaseId, null);
  assert.equal(resolution.pending, null);
  assert.deepEqual(resolution.historical, [
    { key: OLD_COMMON_KEY, purchaseId: "legacy-purchase" },
  ]);
  // The superseded id is never looked up and never run.
  assert.deepEqual(calls, []);
});

test("an id left in the old experiment key is history, never a resume target", { skip }, async () => {
  const { calls, load } = recordingLoad(detail());
  const resolution = await aegis.resolvePendingRequest(
    store({ [OLD_EXPERIMENT_KEY]: "legacy-experiment" }),
    load,
  );
  assert.equal(resolution.status, "none");
  assert.deepEqual(resolution.historical, [
    { key: OLD_EXPERIMENT_KEY, purchaseId: "legacy-experiment" },
  ]);
  assert.deepEqual(calls, []);
});

test("both superseded keys survive next to a verified aegis pending id", { skip }, async () => {
  const { calls, load } = recordingLoad(detail({ budgetUnits: 750000 }));
  const resolution = await aegis.resolvePendingRequest(
    store({
      [OLD_COMMON_KEY]: "legacy-purchase",
      [OLD_EXPERIMENT_KEY]: "legacy-experiment",
      [NEW_KEY]: "aegis-purchase",
    }),
    load,
  );
  assert.equal(resolution.status, "resume");
  assert.equal(resolution.purchaseId, "aegis-purchase");
  assert.deepEqual(calls, ["aegis-purchase"]);
  assert.deepEqual(resolution.historical, [
    { key: OLD_COMMON_KEY, purchaseId: "legacy-purchase" },
    { key: OLD_EXPERIMENT_KEY, purchaseId: "legacy-experiment" },
  ]);
  assert.equal(resolution.pending.budgetUnits, 750000);
  assert.equal(resolution.pending.originalPriority, null);
  assert.equal(resolution.pending.effectivePriority, "price");
});

test("a pending id that cannot be looked up blocks the run", { skip }, async () => {
  const { calls, load } = recordingLoad(new Error("503: evidence api unavailable"));
  const resolution = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "aegis-purchase", [OLD_COMMON_KEY]: "legacy-purchase" }),
    load,
  );
  assert.equal(resolution.status, "blocked");
  assert.equal(resolution.purchaseId, "aegis-purchase");
  assert.equal(resolution.pending, null);
  assert.deepEqual(calls, ["aegis-purchase"]);
  assert.deepEqual(resolution.historical, [
    { key: OLD_COMMON_KEY, purchaseId: "legacy-purchase" },
  ]);
});

test("a stored id proven to be another policy is never resumed", { skip }, async () => {
  const { load } = recordingLoad(detail({ schema: null, purchaseId: "legacy-purchase" }));
  const resolution = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "legacy-purchase" }),
    load,
  );
  assert.equal(resolution.status, "foreign");
  assert.equal(resolution.pending.isAegis, false);
});

test("a not-started budget failure permits a corrected independent request", { skip }, async () => {
  const resolution = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "aegis-budget-failed" }),
    recordingLoad(detail({ paymentStatus: "PAYMENT_NOT_STARTED" })).load,
  );
  assert.equal(resolution.status, "resume");
  assert.equal(aegis.allowsIndependentRequest(resolution.pending), true);
});

test("an unknown payment confirmation blocks a different request", { skip }, async () => {
  const resolution = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "aegis-unknown" }),
    recordingLoad(detail({ paymentStatus: "PAYMENT_CONFIRMATION_UNKNOWN" })).load,
  );
  assert.equal(resolution.status, "resume");
  assert.equal(aegis.allowsIndependentRequest(resolution.pending), false);
});

test("a finished purchase is not re-run from a stale pending key", { skip }, async () => {
  const settled = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "aegis-purchase" }),
    recordingLoad(detail({ paymentStatus: "PAYMENT_SETTLED" })).load,
  );
  assert.equal(settled.status, "resume");
  const audited = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "aegis-purchase" }),
    recordingLoad(detail({ paymentStatus: "PAYMENT_SETTLED", audit: { report_id: "rep-1", severity: "NORMAL", findings: [] } }))
      .load,
  );
  assert.equal(audited.status, "completed");
});

test("an empty stored value is not treated as a pending purchase", { skip }, async () => {
  const { calls, load } = recordingLoad(detail());
  const resolution = await aegis.resolvePendingRequest(
    store({ [NEW_KEY]: "   ", [OLD_COMMON_KEY]: "" }),
    load,
  );
  assert.equal(resolution.status, "none");
  assert.deepEqual(resolution.historical, []);
  assert.deepEqual(calls, []);
});

test("a pending purchase is reused only for the request it was created from", { skip }, async () => {
  const facts = aegis.readStoredRequestFacts(
    detail({ promptHash: "sha256:" + "b".repeat(64), originalPriority: "price", budgetUnits: 500000 }),
  );
  const same = {
    promptHash: "sha256:" + "b".repeat(64),
    priority: "price",
    budgetUnits: 500000,
  };
  assert.equal(aegis.matchesPendingRequest(facts, same), true);
  assert.equal(
    aegis.matchesPendingRequest(facts, { ...same, promptHash: "sha256:" + "c".repeat(64) }),
    false,
  );
  assert.equal(aegis.matchesPendingRequest(facts, { ...same, priority: "speed" }), false);
  // Switching to automatic classification sends no priority at all: a different request.
  assert.equal(aegis.matchesPendingRequest(facts, { ...same, priority: null }), false);
  assert.equal(aegis.matchesPendingRequest(facts, { ...same, budgetUnits: 400000 }), false);
  // An empty budget field cannot be distinguished from the stored wallet limit, so it
  // is disclosed rather than treated as a change.
  assert.equal(aegis.matchesPendingRequest(facts, { ...same, budgetUnits: null }), true);
});

test("an explicit default priority is not the same request as automatic classification", { skip }, () => {
  const automatic = aegis.readStoredRequestFacts(detail({ originalPriority: null }));
  const input = { promptHash: automatic.promptHash, priority: "default", budgetUnits: null };
  assert.equal(aegis.matchesPendingRequest(automatic, input), false);
  assert.equal(aegis.matchesPendingRequest(automatic, { ...input, priority: null }), true);
});

test("the prompt fingerprint matches the server's stored hash form", { skip }, async () => {
  const prompt = "가장 싸게 처리해줘";
  const expected = `sha256:${createHash("sha256").update(prompt, "utf8").digest("hex")}`;
  assert.equal(await aegis.promptHash(prompt), expected);
  assert.match(await aegis.promptHash(""), /^sha256:[0-9a-f]{64}$/);
});

test("a pending lookup still in flight blocks any run", { skip }, async () => {
  let release = () => {};
  const answered = new Promise((resolve) => {
    release = resolve;
  });
  const calls = [];
  const load = async (purchaseId) => {
    calls.push(purchaseId);
    await answered;
    return detail();
  };
  // The store fails on any write, so a delayed lookup cannot overwrite the stored id.
  const resolving = aegis.resolvePendingRequest(store({ [NEW_KEY]: "aegis-purchase" }), load);
  let settled = false;
  void resolving.then(() => {
    settled = true;
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(settled, false, "the lookup must still be pending for this regression");
  assert.deepEqual(calls, ["aegis-purchase"]);
  // This is the state the surface holds while the GET is outstanding: neither a new
  // purchase nor a resume may start from it.
  assert.equal(aegis.canSubmitRequest(null, { busy: false, acknowledged: true }), false);

  release();
  const resolution = await resolving;
  assert.equal(resolution.status, "resume");
  assert.equal(aegis.canSubmitRequest(resolution, { busy: false, acknowledged: true }), true);
});

test("the submit gate refuses every unverified or busy state", { skip }, () => {
  const state = { busy: false, acknowledged: true };
  const resolution = (status) => ({ status, purchaseId: "aegis-purchase", pending: null, historical: [] });
  assert.equal(aegis.canSubmitRequest(null, state), false);
  assert.equal(aegis.canSubmitRequest(resolution("blocked"), state), false);
  assert.equal(aegis.canSubmitRequest(resolution("resume"), { ...state, busy: true }), false);
  assert.equal(aegis.canSubmitRequest(resolution("resume"), { ...state, acknowledged: false }), false);
  assert.equal(aegis.canSubmitRequest(resolution("resume"), state), true);
  assert.equal(aegis.canSubmitRequest(resolution("none"), state), true);
  // A stored id of another policy or a finished purchase is not resumable, but a new
  // request may still be created.
  assert.equal(aegis.canSubmitRequest(resolution("foreign"), state), true);
  assert.equal(aegis.canSubmitRequest(resolution("completed"), state), false);
});
