import assert from "node:assert/strict";
import test from "node:test";

import type { Address, Hex } from "viem";

import {
  Erc8004PolicyError,
  Erc8004ReputationPublisher,
  Erc8004Service,
  type Erc8004Contracts,
} from "../src/index.js";

const OWNER = "0x0000000000000000000000000000000000000001" as Address;
const AGENT = "0x0000000000000000000000000000000000000002" as Address;
const CLIENT = "0x0000000000000000000000000000000000000003" as Address;
const HASH = `0x${"44".repeat(32)}` as Hex;

function fakeContracts() {
  const feedback: Parameters<Erc8004Contracts["giveFeedback"]>[0][] = [];
  const contracts: Erc8004Contracts = {
    async identity(agentId) { return { agentId, owner: OWNER, agentWallet: AGENT }; },
    async summary() { return { count: 2n, value: 100n, valueDecimals: 0 }; },
    async giveFeedback(args) { feedback.push(args); return HASH; },
    async findFeedback() { return null; },
  };
  return { contracts, feedback };
}

test("identity binds the signed quote to the registered agent wallet", async () => {
  const { contracts } = fakeContracts();
  const service = new Erc8004Service(contracts, CLIENT);
  assert.equal((await service.verifyQuoteSigner(7n, AGENT)).owner, OWNER);
  await assert.rejects(
    () => service.verifyQuoteSigner(7n, CLIENT),
    Erc8004PolicyError,
  );
});

test("objective feedback is exactly 100 or 0 and hash-bound", async () => {
  const { contracts, feedback } = fakeContracts();
  const service = new Erc8004Service(contracts, CLIENT);
  await service.submitObjectiveFeedback({
    agentId: 7n,
    verifiedSuccess: true,
    feedbackHash: HASH,
    feedbackUri: "ipfs://audit-bundle",
  });
  await service.submitObjectiveFeedback({
    agentId: 8n,
    verifiedSuccess: false,
    feedbackHash: HASH,
  });
  assert.deepEqual(feedback.map((item) => item.value), [100n, 0n]);
  assert.ok(feedback.every((item) => item.valueDecimals === 0));
  assert.ok(feedback.every((item) => item.feedbackHash === HASH));
});

test("self-feedback and empty trusted-client aggregation are rejected", async () => {
  const { contracts } = fakeContracts();
  const service = new Erc8004Service(contracts, OWNER);
  await assert.rejects(
    () => service.submitObjectiveFeedback({
      agentId: 7n,
      verifiedSuccess: true,
      feedbackHash: HASH,
    }),
    /self-feedback/,
  );
  assert.throws(() => service.summary(7n, []), /must not be empty/);
});

test("confirmed objective feedback records the exact audit bundle and transaction", async () => {
  const { contracts } = fakeContracts();
  const service = new Erc8004Service(contracts, CLIENT);
  const records: unknown[] = [];
  const publisher = new Erc8004ReputationPublisher({
    service,
    evidence: {
      async prepare(_purchaseId, agentId) {
        return { agentId, objectiveValue: 100 as const, feedbackHash: HASH };
      },
      async record(args) { records.push(args); },
    },
    receipts: { async confirm(args) {
      assert.equal(args.transactionHash, HASH);
      assert.equal(args.registryAddress, AGENT);
      assert.equal(args.agentId, 1n);
      assert.equal(args.objectiveValue, 100);
      assert.equal(args.feedbackHash, HASH);
      return true;
    } },
    chainId: 84532,
    registryAddress: AGENT,
  });
  assert.equal(await publisher.publish({
    purchaseId: "purchase-1", agentId: 1n,
  }), HASH);
  assert.equal(records.length, 1);
  assert.deepEqual(records[0], {
    purchaseId: "purchase-1",
    agentId: 1n,
    objectiveValue: 100,
    feedbackHash: `0x${"44".repeat(32)}`,
    transactionHash: HASH,
    chainId: 84532,
    registryAddress: AGENT,
  });
});

test("recorded or on-chain feedback is reused without a second write", async () => {
  const { contracts, feedback } = fakeContracts();
  let recovered = false;
  contracts.findFeedback = async () => recovered ? HASH : null;
  const service = new Erc8004Service(contracts, CLIENT);
  const records: unknown[] = [];
  const evidence = {
    existing: undefined as Hex | undefined,
    async prepare(_purchaseId: string, agentId: bigint) {
      return {
        agentId,
        objectiveValue: 100 as const,
        feedbackHash: HASH,
        ...(this.existing === undefined ? {} : { transactionHash: this.existing }),
      };
    },
    async record(args: unknown) { records.push(args); },
  };
  const publisher = new Erc8004ReputationPublisher({
    service,
    evidence,
    receipts: { async confirm() { return true; } },
    chainId: 84532,
    registryAddress: AGENT,
  });
  recovered = true;
  assert.equal(await publisher.publish({ purchaseId: "purchase-1", agentId: 1n }), HASH);
  assert.equal(feedback.length, 0);
  assert.equal(records.length, 1);
  evidence.existing = HASH;
  assert.equal(await publisher.publish({ purchaseId: "purchase-1", agentId: 1n }), HASH);
  assert.equal(feedback.length, 0);
  assert.equal(records.length, 1);
});

test("concurrent reputation publication coalesces into one irreversible write", async () => {
  const { contracts, feedback } = fakeContracts();
  contracts.giveFeedback = async (args) => {
    feedback.push(args);
    await new Promise((resolve) => setTimeout(resolve, 5));
    return HASH;
  };
  const publisher = new Erc8004ReputationPublisher({
    service: new Erc8004Service(contracts, CLIENT),
    evidence: {
      async prepare(_purchaseId, agentId) {
        return { agentId, objectiveValue: 100 as const, feedbackHash: HASH };
      },
      async record() {},
    },
    receipts: { async confirm() { return true; } },
    chainId: 84532,
    registryAddress: AGENT,
  });
  const results = await Promise.all([
    publisher.publish({ purchaseId: "purchase-1", agentId: 1n }),
    publisher.publish({ purchaseId: "purchase-1", agentId: 1n }),
  ]);
  assert.deepEqual(results, [HASH, HASH]);
  assert.equal(feedback.length, 1);
});
