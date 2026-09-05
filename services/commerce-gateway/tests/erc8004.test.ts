import assert from "node:assert/strict";
import test from "node:test";

import { keccak256, stringToHex, type Address, type Hex } from "viem";

import {
  Erc8004PolicyError,
  Erc8004ReputationPublisher,
  Erc8004Service,
  FEEDBACK_TAG1,
  FEEDBACK_TAG2,
  type ConfirmedFeedbackProof,
  type Erc8004Contracts,
  type ReputationOutboxApi,
  type ReputationPublishJob,
} from "../src/index.js";

const OWNER = "0x0000000000000000000000000000000000000001" as Address;
const AGENT = "0x0000000000000000000000000000000000000002" as Address;
const CLIENT = "0x0000000000000000000000000000000000000003" as Address;
const REGISTRY = "0x0000000000000000000000000000000000000004" as Address;
const HASH = `0x${"44".repeat(32)}` as Hex;
const FINGERPRINT = `sha256:${"22".repeat(32)}`;
const FEEDBACK_HASH = keccak256(stringToHex(FINGERPRINT));

function fakeContracts() {
  const feedback: Parameters<Erc8004Contracts["giveFeedback"]>[0][] = [];
  const contracts: Erc8004Contracts = {
    async identity(agentId) { return { agentId, owner: OWNER, agentWallet: AGENT }; },
    async summary() { return { count: 2n, value: 100n, valueDecimals: 0 }; },
    async giveFeedback(args) { feedback.push(args); return HASH; },
    async findFeedback() { return null; },
    async queryFeedback() { return []; },
  };
  return { contracts, feedback };
}

function job(overrides: Partial<ReputationPublishJob> = {}): ReputationPublishJob {
  return {
    jobId: "job-1",
    status: "PENDING",
    identity: {
      chainId: 84532,
      registryAddress: REGISTRY,
      purchaseId: "purchase-1",
      sellerAgentId: "seller-gemini",
      tag1: FEEDBACK_TAG1,
      tag2: FEEDBACK_TAG2,
    },
    identityHash: `sha256:${"11".repeat(32)}`,
    payloadFingerprint: FINGERPRINT,
    decision: {
      decision: "PUBLISH",
      value: 100,
      reasonCodes: ["EXACT_TRANSFER_CONFIRMED"],
      auditBundleHash: `sha256:${"33".repeat(32)}`,
      rulesetVersion: "phase6-v1",
      sellerAgentId: "seller-gemini",
      erc8004AgentId: "1",
    },
    attemptCount: 0,
    workerId: null,
    leaseExpiresAt: null,
    feedbackHash: null,
    transactionRef: null,
    receiptProofRef: null,
    createdAt: "2026-01-01T00:00:00.000Z",
    updatedAt: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function proof(overrides: Partial<ConfirmedFeedbackProof> = {}): ConfirmedFeedbackProof {
  return {
    transactionRef: {
      kind: "EVM",
      hash: HASH,
      chainId: 84532,
      evidenceSource: "BASE_SEPOLIA_VERIFIED",
      blockNumber: 4242,
      logIndex: 3,
    },
    receiptProofRef: `sha256:${"55".repeat(32)}`,
    clientAddress: CLIENT,
    erc8004AgentId: "1",
    value: 100,
    valueDecimals: 0,
    feedbackHash: FEEDBACK_HASH,
    blockNumber: 4242,
    logIndex: 3,
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
    feedbackUri: `sha256:${"33".repeat(32)}`,
    ...overrides,
  };
}

/** A single-job durable outbox: it hands the job out once, exactly like a leased row. */
function fakeOutbox(initial: ReputationPublishJob) {
  const calls: { name: string; args: unknown }[] = [];
  let current = initial;
  let claimable = true;
  const outbox: ReputationOutboxApi = {
    async claim(args) {
      calls.push({ name: "claim", args });
      if (!claimable) return null;
      claimable = false;
      current = { ...current, status: "LEASED", workerId: args.workerId };
      return current;
    },
    async get(jobId) {
      calls.push({ name: "get", args: jobId });
      return current.jobId === jobId ? current : null;
    },
    async markPrepared(args) {
      calls.push({ name: "markPrepared", args });
      current = {
        ...current,
        status: "PREPARED",
        feedbackHash: args.feedbackHash,
        transactionRef: args.transactionRef ?? null,
      };
      return current;
    },
    async markSubmittedUnknown(args) {
      calls.push({ name: "markSubmittedUnknown", args });
      current = { ...current, status: "SUBMITTED_UNKNOWN", transactionRef: args.transactionRef };
      return current;
    },
    async markConfirmed(args) {
      calls.push({ name: "markConfirmed", args });
      current = {
        ...current,
        status: "CONFIRMED",
        transactionRef: args.proof.transactionRef,
        receiptProofRef: args.proof.receiptProofRef,
        feedbackHash: args.proof.feedbackHash,
      };
      return current;
    },
  };
  return { outbox, calls };
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
  assert.ok(feedback.every((item) => item.tag1 === FEEDBACK_TAG1 && item.tag2 === FEEDBACK_TAG2));
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
  const { contracts, feedback } = fakeContracts();
  const { outbox, calls } = fakeOutbox(job());
  const publisher = new Erc8004ReputationPublisher({
    service: new Erc8004Service(contracts, CLIENT),
    outbox,
    receipts: { async confirm(args) {
      assert.equal(args.transactionHash, HASH);
      assert.equal(args.registryAddress, REGISTRY);
      assert.equal(args.agentId, 1n);
      assert.equal(args.objectiveValue, 100);
      assert.equal(args.feedbackHash, FEEDBACK_HASH);
      assert.equal(args.evidenceSource, "BASE_SEPOLIA_VERIFIED");
      return proof();
    } },
    chainId: 84532,
    registryAddress: REGISTRY,
    workerId: "worker-a",
    writeMode: "live",
  });
  const driven = await publisher.publishClaimed();
  assert.equal(driven?.status, "CONFIRMED");
  // The audit bundle hash is the only feedback URI ever written.
  assert.equal(feedback.length, 1);
  assert.equal(feedback[0]?.feedbackUri, `sha256:${"33".repeat(32)}`);
  assert.equal(feedback[0]?.feedbackHash, FEEDBACK_HASH);
  assert.deepEqual(calls.map((call) => call.name), [
    "claim",
    "markPrepared",
    "markConfirmed",
  ]);
  const confirmed = calls[2]?.args as { proof: ConfirmedFeedbackProof; workerId: string };
  assert.equal(confirmed.workerId, "worker-a");
  assert.deepEqual(confirmed.proof.transactionRef, {
    kind: "EVM",
    hash: HASH,
    chainId: 84532,
    evidenceSource: "BASE_SEPOLIA_VERIFIED",
    blockNumber: 4242,
    logIndex: 3,
  });
});

test("recorded or on-chain feedback is reused without a second write", async () => {
  const { contracts, feedback } = fakeContracts();
  contracts.findFeedback = async () => ({
    transactionHash: HASH,
    blockNumber: 4242,
    logIndex: 3,
  });
  const { outbox, calls } = fakeOutbox(job());
  const sources: string[] = [];
  const publisher = new Erc8004ReputationPublisher({
    service: new Erc8004Service(contracts, CLIENT),
    outbox,
    receipts: { async confirm(args) {
      sources.push(args.evidenceSource);
      return proof({
        transactionRef: {
          kind: "EVM",
          hash: HASH,
          chainId: 84532,
          evidenceSource: "HISTORICAL_ON_CHAIN",
          blockNumber: 4242,
          logIndex: 3,
        },
      });
    } },
    chainId: 84532,
    registryAddress: REGISTRY,
    workerId: "worker-a",
    writeMode: "live",
  });
  const driven = await publisher.publishClaimed();
  assert.equal(driven?.status, "CONFIRMED");
  assert.equal(feedback.length, 0);
  assert.deepEqual(sources, ["HISTORICAL_ON_CHAIN"]);
  assert.deepEqual(calls.map((call) => call.name), ["claim", "markConfirmed"]);

  // An already CONFIRMED job never reaches the chain again.
  const again = await publisher.drive(driven);
  assert.equal(again.status, "CONFIRMED");
  assert.equal(feedback.length, 0);
  assert.equal(calls.filter((call) => call.name === "markConfirmed").length, 1);
});

test("concurrent reputation publication is serialized by the durable claim", async () => {
  const { contracts, feedback } = fakeContracts();
  // The ES2022 lib this package targets has no `Promise.withResolvers`, so the gates are
  // built with the executor form. They pin the first worker mid-broadcast with no timers.
  let writeReached = () => {};
  let releaseWrite = () => {};
  const atWrite = new Promise<void>((resolve) => { writeReached = resolve; });
  const writeInFlight = new Promise<void>((resolve) => { releaseWrite = resolve; });
  contracts.giveFeedback = async (args) => {
    feedback.push(args);
    writeReached();
    await writeInFlight;
    return HASH;
  };
  const { outbox, calls } = fakeOutbox(job());
  const publisher = new Erc8004ReputationPublisher({
    service: new Erc8004Service(contracts, CLIENT),
    outbox,
    receipts: { async confirm() { return proof(); } },
    chainId: 84532,
    registryAddress: REGISTRY,
    workerId: "worker-a",
    writeMode: "live",
  });
  const first = publisher.publishClaimed();
  await atWrite;
  // A second worker claims while the first broadcast is still in flight.
  const second = await publisher.publishClaimed();
  assert.equal(second, null);
  assert.equal(feedback.length, 1);
  releaseWrite();
  assert.equal((await first)?.status, "CONFIRMED");
  // The second claim found nothing claimable: no in-process bookkeeping is involved.
  assert.equal(feedback.length, 1);
  assert.equal(calls.filter((call) => call.name === "claim").length, 2);
  assert.equal(calls.filter((call) => call.name === "markPrepared").length, 1);
});
