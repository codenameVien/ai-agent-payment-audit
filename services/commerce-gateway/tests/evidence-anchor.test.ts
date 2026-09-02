import assert from "node:assert/strict";
import test from "node:test";

import type { Address, Hex } from "viem";

import { EvidenceAnchorService } from "../src/index.js";

const ZERO = `0x${"00".repeat(32)}` as Hex;
const HEAD = `0x${"11".repeat(32)}` as Hex;
const TX = `0x${"22".repeat(32)}` as Hex;
const CONTRACT = "0x0000000000000000000000000000000000000005" as Address;

test("external anchor writes the verified head then records the exact transaction", async () => {
  const recorded: unknown[] = [];
  let anchored: unknown;
  const apiHead = `sha256:${"11".repeat(32)}` as const;
  const service = new EvidenceAnchorService({
    api: {
      async head(purchaseId) {
        return { purchaseId, eventCount: 7, headEventHash: apiHead };
      },
      async record(args) { recorded.push(args); },
    },
    contract: {
      async latest() { return { eventCount: 0n, headHash: ZERO }; },
      async findConfirmed() { return null; },
      async anchor(args) { anchored = args; return { transactionHash: TX, confirmed: true }; },
    },
    chainId: 84532,
    contractAddress: CONTRACT,
  });
  assert.equal(await service.anchor("purchase-1"), TX);
  assert.deepEqual((anchored as { eventCount: bigint }).eventCount, 7n);
  assert.deepEqual((anchored as { previousHeadHash: Hex }).previousHeadHash, ZERO);
  assert.deepEqual((anchored as { headHash: Hex }).headHash, HEAD);
  assert.equal(recorded.length, 1);
  assert.deepEqual((recorded[0] as { transactionHash: Hex }).transactionHash, TX);
  assert.deepEqual(
    (recorded[0] as { checkpoint: { headEventHash: string } }).checkpoint.headEventHash,
    apiHead,
  );
});

test("stale evidence head cannot overwrite a newer on-chain checkpoint", async () => {
  const service = new EvidenceAnchorService({
    api: {
      async head(purchaseId) {
        return { purchaseId, eventCount: 7, headEventHash: HEAD };
      },
      async record() { throw new Error("must not record"); },
    },
    contract: {
      async latest() { return { eventCount: 8n, headHash: HEAD }; },
      async findConfirmed() { return null; },
      async anchor() { throw new Error("must not write"); },
    },
    chainId: 84532,
    contractAddress: CONTRACT,
  });
  await assert.rejects(() => service.anchor("purchase-1"), /not newer/);
});

test("unconfirmed or reverted anchor is never recorded in MongoDB", async () => {
  let recorded = false;
  const service = new EvidenceAnchorService({
    api: {
      async head(purchaseId) {
        return { purchaseId, eventCount: 1, headEventHash: HEAD };
      },
      async record() { recorded = true; },
    },
    contract: {
      async latest() { return { eventCount: 0n, headHash: ZERO }; },
      async findConfirmed() { return null; },
      async anchor() { return { transactionHash: TX, confirmed: false }; },
    },
    chainId: 84532,
    contractAddress: CONTRACT,
  });
  await assert.rejects(() => service.anchor("purchase-1"), /not confirmed exactly/);
  assert.equal(recorded, false);
});

test("confirmed anchor is recovered after a crash before evidence recording", async () => {
  const recorded: unknown[] = [];
  let writes = 0;
  const service = new EvidenceAnchorService({
    api: {
      async head(purchaseId) {
        return { purchaseId, eventCount: 7, headEventHash: HEAD };
      },
      async record(args) { recorded.push(args); },
    },
    contract: {
      async latest() { return { eventCount: 7n, headHash: HEAD }; },
      async findConfirmed() { return TX; },
      async anchor() { writes += 1; return { transactionHash: TX, confirmed: true }; },
    },
    chainId: 84532,
    contractAddress: CONTRACT,
  });
  assert.equal(await service.anchor("purchase-1"), TX);
  assert.equal(writes, 0);
  assert.equal(recorded.length, 1);
});
