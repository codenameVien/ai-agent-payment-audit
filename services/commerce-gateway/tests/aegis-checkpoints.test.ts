import assert from "node:assert/strict";
import test from "node:test";
import { AegisCheckpoints, checkpointsFromEnv, type CheckpointRecord } from "../src/aegis/checkpoints.js";
import type { EvidenceAnchorContract } from "../src/evidence-anchor.js";
import type { Hex } from "viem";

const head = `sha256:${"a".repeat(64)}`;
const address = `0x${"b".repeat(40)}` as const;
const tx = `0x${"c".repeat(64)}` as const;
function fixture(mode: "mock" | "live" = "mock", contract?: EvidenceAnchorContract) {
  let record: CheckpointRecord | null = null;
  let writes = 0;
  const fetchImpl = (async (_url: unknown, init?: RequestInit) => {
    if (init?.method === "POST") { record = JSON.parse(String(init.body)); writes++; }
    return Response.json({ purchaseId: "p1", phase: "decision", eventCount: 3, headEventHash: head, record });
  }) as typeof fetch;
  return { service: new AegisCheckpoints({ mode, baseUrl: "http://evidence", internalToken: "test",
    fetchImpl, contract, contractAddress: mode === "live" ? address : undefined }),
    writes: () => writes, set: (value: CheckpointRecord) => { record = value; } };
}

test("mock checkpoint has no fabricated chain proof and same phase replay is idempotent", async () => {
  const f = fixture();
  await assert.rejects(f.service.requireDecision("p1"), /required/);
  const [first, concurrent] = await Promise.all([f.service.anchor("p1", "decision"), f.service.anchor("p1", "decision")]);
  assert.deepEqual(first, concurrent);
  assert.equal(first.mode, "mock");
  assert.equal(first.transactionHash, undefined);
  assert.equal(first.chainId, undefined);
  await f.service.requireDecision("p1");
  await f.service.anchor("p1", "decision");
  assert.equal(f.writes(), 1);
});

test("live requires separate approval and validates writer configuration", () => {
  assert.throws(() => checkpointsFromEnv({ AEGIS_CHECKPOINT_MODE: "live" }), /separate approval/);
  assert.throws(() => checkpointsFromEnv({ AEGIS_CHECKPOINT_MODE: "live", AEGIS_ANCHOR_WRITE_APPROVED: "yes" }), /configuration required/);
  assert.throws(() => checkpointsFromEnv({ AEGIS_CHECKPOINT_MODE: "typo" }), /invalid/);
});

test("live confirmation is recorded only after exact adapter confirmation", async () => {
  let calls = 0;
  const contract: EvidenceAnchorContract = {
    latest: async () => ({ eventCount: 0n, headHash: `0x${"0".repeat(64)}` as Hex }),
    findConfirmed: async () => calls ? tx : null,
    anchor: async args => {
      calls++;
      assert.equal(args.eventCount, 3n);
      assert.equal(args.headHash, `0x${"a".repeat(64)}`);
      return { transactionHash: tx, confirmed: true };
    },
  };
  const f = fixture("live", contract);
  const result = await f.service.anchor("p1", "decision");
  assert.equal(result.mode, "live");
  assert.equal(result.transactionHash, tx);
  await f.service.anchor("p1", "decision");
  assert.equal(calls, 1);
  assert.equal(f.writes(), 1);
});

test("unconfirmed anchor cannot unlock payment or fabricate a record", async () => {
  const contract: EvidenceAnchorContract = {
    latest: async () => ({ eventCount: 0n, headHash: `0x${"0".repeat(64)}` as Hex }),
    findConfirmed: async () => null,
    anchor: async () => ({ transactionHash: tx, confirmed: false }),
  };
  const f = fixture("live", contract);
  await assert.rejects(f.service.anchor("p1", "decision"), /not confirmed/);
  assert.equal(f.writes(), 0);
  await assert.rejects(f.service.requireDecision("p1"), /required/);
});

test("a mock or mismatched hash record never unlocks a live payment", async () => {
  const f = fixture("live");
  f.set({ mode: "mock", eventCount: 3, headEventHash: head });
  await assert.rejects(f.service.requireDecision("p1"), /mismatch/);
  f.set({ mode: "live", eventCount: 3, headEventHash: "sha256:bad" });
  await assert.rejects(f.service.requireDecision("p1"), /mismatch/);
});

test("rewritten Mongo checkpoint metadata cannot replace the independent live anchor", async () => {
  const f = fixture("live", {
    latest: async () => { throw new Error("not used for historical comparison"); },
    findConfirmed: async () => null,
    anchor: async () => { throw new Error("must not submit after evidence mismatch"); },
  });
  f.set({ mode: "live", eventCount: 3, headEventHash: head, transactionHash: tx,
    contractAddress: address, chainId: 84532 });
  await assert.rejects(f.service.requireDecision("p1"), /differs/);
  await assert.rejects(f.service.anchor("p1", "decision"), /differs/);
  await assert.rejects(f.service.anchor("p1", "audit"), /differs/);
  assert.equal(f.writes(), 0);
});
