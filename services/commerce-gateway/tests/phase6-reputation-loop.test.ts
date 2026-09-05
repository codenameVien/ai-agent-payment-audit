import assert from "node:assert/strict";
import test from "node:test";

import { keccak256, stringToHex, type Address, type Hex } from "viem";

import {
  Erc8004PolicyError,
  Erc8004ReputationPublisher,
  Erc8004Service,
  FEEDBACK_TAG1,
  FEEDBACK_TAG2,
  ReputationOutboxConflictError,
  ViemErc8004Contracts,
  type ConfirmedFeedbackProof,
  type Erc8004Contracts,
  type RawFeedbackEvent,
  type ReputationOutboxApi,
  type ReputationPublishJob,
  type TransactionRef,
} from "../src/index.js";

const OWNER = "0x0000000000000000000000000000000000000001" as Address;
const AGENT_WALLET = "0x0000000000000000000000000000000000000002" as Address;
const CLIENT = "0x0000000000000000000000000000000000000003" as Address;
const REGISTRY = "0x0000000000000000000000000000000000000004" as Address;
const OUTSIDER = "0x00000000000000000000000000000000000000aA" as Address;
const TX = `0x${"77".repeat(32)}` as Hex;
const FINGERPRINT = `sha256:${"22".repeat(32)}`;
const FEEDBACK_HASH = keccak256(stringToHex(FINGERPRINT));
const AUDIT_BUNDLE = `sha256:${"33".repeat(32)}`;

/** Every chain call the publisher can make, recorded in the order it happened. */
interface ChainJournal {
  journal: string[];
  giveFeedback: Parameters<Erc8004Contracts["giveFeedback"]>[0][];
  contracts: Erc8004Contracts;
}

function fakeContracts(args: {
  journal: string[];
  found?: { transactionHash: Hex; blockNumber: number; logIndex: number } | null;
  events?: RawFeedbackEvent[];
}): ChainJournal {
  const giveFeedback: Parameters<Erc8004Contracts["giveFeedback"]>[0][] = [];
  const contracts: Erc8004Contracts = {
    async identity(agentId) {
      return { agentId, owner: OWNER, agentWallet: AGENT_WALLET };
    },
    async summary() {
      return { count: 1n, value: 100n, valueDecimals: 0 };
    },
    async giveFeedback(call) {
      args.journal.push("giveFeedback");
      giveFeedback.push(call);
      return TX;
    },
    async findFeedback() {
      args.journal.push("findFeedback");
      return args.found ?? null;
    },
    async queryFeedback() {
      args.journal.push("queryFeedback");
      return args.events ?? [];
    },
  };
  return { journal: args.journal, giveFeedback, contracts };
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
      auditBundleHash: AUDIT_BUNDLE,
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
      hash: TX,
      chainId: 84532,
      evidenceSource: "BASE_SEPOLIA_VERIFIED",
      blockNumber: 900,
      logIndex: 2,
    },
    receiptProofRef: `sha256:${"55".repeat(32)}`,
    clientAddress: CLIENT,
    erc8004AgentId: "1",
    value: 100,
    valueDecimals: 0,
    feedbackHash: FEEDBACK_HASH,
    blockNumber: 900,
    logIndex: 2,
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
    feedbackUri: AUDIT_BUNDLE,
    ...overrides,
  };
}

/**
 * A durable outbox stand-in. It hands a job to exactly one claimer and journals every
 * transition next to the chain calls, so ordering is observable.
 */
function fakeOutbox(args: {
  initial: ReputationPublishJob;
  journal: string[];
  conflictOn?: "prepared" | "confirmed" | "submitted-unknown";
}) {
  const calls: { name: string; args: unknown }[] = [];
  let current = args.initial;
  let claimable = true;
  const outbox: ReputationOutboxApi = {
    async claim(claimArgs) {
      args.journal.push("claim");
      calls.push({ name: "claim", args: claimArgs });
      if (!claimable) return null;
      claimable = false;
      current = { ...current, status: "LEASED", workerId: claimArgs.workerId };
      return current;
    },
    async get(jobId) {
      calls.push({ name: "get", args: jobId });
      return current.jobId === jobId ? current : null;
    },
    async markPrepared(call) {
      args.journal.push("markPrepared");
      calls.push({ name: "markPrepared", args: call });
      if (args.conflictOn === "prepared") {
        throw new ReputationOutboxConflictError("lease expired");
      }
      current = {
        ...current,
        status: "PREPARED",
        feedbackHash: call.feedbackHash,
        transactionRef: call.transactionRef ?? null,
      };
      return current;
    },
    async markSubmittedUnknown(call) {
      args.journal.push("markSubmittedUnknown");
      calls.push({ name: "markSubmittedUnknown", args: call });
      if (args.conflictOn === "submitted-unknown") {
        throw new ReputationOutboxConflictError("lease expired");
      }
      current = {
        ...current,
        status: "SUBMITTED_UNKNOWN",
        transactionRef: call.transactionRef,
      };
      return current;
    },
    async markConfirmed(call) {
      args.journal.push("markConfirmed");
      calls.push({ name: "markConfirmed", args: call });
      if (args.conflictOn === "confirmed") {
        throw new ReputationOutboxConflictError("payload fingerprint changed");
      }
      current = {
        ...current,
        status: "CONFIRMED",
        transactionRef: call.proof.transactionRef,
        receiptProofRef: call.proof.receiptProofRef,
        feedbackHash: call.proof.feedbackHash,
      };
      return current;
    },
  };
  return { outbox, calls, current: () => current };
}

function publisher(args: {
  contracts: Erc8004Contracts;
  outbox: ReputationOutboxApi;
  confirm: (call: {
    transactionHash: Hex;
    evidenceSource: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
  }) => ConfirmedFeedbackProof | null;
  writeMode?: "disabled" | "live";
  journal: string[];
}): Erc8004ReputationPublisher {
  return new Erc8004ReputationPublisher({
    service: new Erc8004Service(args.contracts, CLIENT),
    outbox: args.outbox,
    receipts: {
      async confirm(call) {
        args.journal.push("confirm");
        return args.confirm(call);
      },
    },
    chainId: 84532,
    registryAddress: REGISTRY,
    workerId: "worker-a",
    writeMode: args.writeMode ?? "live",
  });
}

test("a DEFER decision never contacts the chain", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox, calls } = fakeOutbox({
    initial: job({ decision: { ...job().decision, decision: "DEFER", value: null } }),
    journal,
  });
  const driven = await publisher({ ...chain, outbox, confirm: () => proof(), journal })
    .publishClaimed();
  assert.equal(driven?.status, "LEASED");
  assert.equal(chain.giveFeedback.length, 0);
  assert.deepEqual(journal, ["claim"]);
  assert.deepEqual(calls.map((call) => call.name), ["claim"]);
});

test("a terminal job is returned untouched", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox } = fakeOutbox({ initial: job(), journal });
  const loop = publisher({ ...chain, outbox, confirm: () => proof(), journal });
  for (const status of ["CONFIRMED", "DEFERRED", "CONFLICT"] as const) {
    const driven = await loop.drive(job({ status }));
    assert.equal(driven.status, status);
  }
  assert.deepEqual(journal, []);
});

test("a disabled writer refuses to submit but still completes an on-chain recovery", async () => {
  const journal: string[] = [];
  const recovered = fakeContracts({
    journal,
    found: { transactionHash: TX, blockNumber: 900, logIndex: 2 },
  });
  const recoveredOutbox = fakeOutbox({ initial: job(), journal });
  const sources: string[] = [];
  const driven = await publisher({
    ...recovered,
    outbox: recoveredOutbox.outbox,
    writeMode: "disabled",
    journal,
    confirm: (call) => {
      sources.push(call.evidenceSource);
      return proof({
        transactionRef: {
          kind: "EVM",
          hash: TX,
          chainId: 84532,
          evidenceSource: "HISTORICAL_ON_CHAIN",
          blockNumber: 900,
          logIndex: 2,
        },
      });
    },
  }).publishClaimed();
  assert.equal(driven?.status, "CONFIRMED");
  assert.equal(recovered.giveFeedback.length, 0);
  assert.deepEqual(sources, ["HISTORICAL_ON_CHAIN"]);

  // Nothing on chain: the disabled writer refuses instead of publishing.
  const blank: string[] = [];
  const fresh = fakeContracts({ journal: blank });
  const freshOutbox = fakeOutbox({ initial: job(), journal: blank });
  await assert.rejects(
    () => publisher({
      ...fresh,
      outbox: freshOutbox.outbox,
      writeMode: "disabled",
      journal: blank,
      confirm: () => proof(),
    }).publishClaimed(),
    /reputation writer is disabled/,
  );
  assert.equal(fresh.giveFeedback.length, 0);
  assert.deepEqual(blank, ["claim", "findFeedback"]);
});

test("the intent to submit is persisted before the irreversible write", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox, calls } = fakeOutbox({ initial: job(), journal });
  const driven = await publisher({ ...chain, outbox, confirm: () => proof(), journal })
    .publishClaimed();
  assert.equal(driven?.status, "CONFIRMED");
  assert.deepEqual(journal, [
    "claim",
    "findFeedback",
    "markPrepared",
    "giveFeedback",
    "confirm",
    "markConfirmed",
  ]);
  const prepared = calls.find((call) => call.name === "markPrepared")?.args as {
    transactionRef?: TransactionRef;
    feedbackHash: Hex;
    payloadFingerprint: string;
  };
  // No pre-broadcast reference is invented: an EVM hash only exists after the submission,
  // and fabricating a SYNTHETIC_LOCAL identifier for a live write would fake provenance.
  assert.equal(prepared.transactionRef, undefined);
  assert.equal(prepared.feedbackHash, FEEDBACK_HASH);
  assert.equal(prepared.payloadFingerprint, FINGERPRINT);
  // The reference is bound exactly once, by the confirmation that discovered it.
  const confirmed = calls.find((call) => call.name === "markConfirmed")?.args as {
    proof: { transactionRef: TransactionRef };
  };
  assert.equal(confirmed.proof.transactionRef.kind, "EVM");
  assert.equal(chain.giveFeedback[0]?.feedbackUri, AUDIT_BUNDLE);
});

test("the derived feedback hash is a deterministic function of the fingerprint", async () => {
  const hashes: Hex[] = [];
  for (const attempt of [0, 1]) {
    const journal: string[] = [];
    const chain = fakeContracts({ journal });
    const { outbox, calls } = fakeOutbox({ initial: job({ attemptCount: attempt }), journal });
    await publisher({ ...chain, outbox, confirm: () => proof(), journal }).publishClaimed();
    const prepared = calls.find((call) => call.name === "markPrepared")?.args as {
      feedbackHash: Hex;
    };
    hashes.push(prepared.feedbackHash);
  }
  assert.equal(hashes[0], hashes[1]);
  assert.equal(hashes[0], FEEDBACK_HASH);
});

test("a PREPARED job is recovered without a second write", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const recordedRef: TransactionRef = {
    kind: "EVM",
    hash: TX,
    chainId: 84532,
    evidenceSource: "BASE_SEPOLIA_VERIFIED",
  };
  const { outbox, calls } = fakeOutbox({
    initial: job({
      status: "PREPARED",
      feedbackHash: FEEDBACK_HASH,
      transactionRef: recordedRef,
    }),
    journal,
  });
  const confirmed: Hex[] = [];
  const driven = await publisher({
    ...chain,
    outbox,
    journal,
    confirm: (call) => {
      confirmed.push(call.transactionHash);
      return proof();
    },
  }).drive(job({
    status: "PREPARED",
    feedbackHash: FEEDBACK_HASH,
    transactionRef: recordedRef,
  }));
  assert.equal(driven.status, "CONFIRMED");
  assert.equal(chain.giveFeedback.length, 0);
  assert.deepEqual(confirmed, [TX]);
  assert.deepEqual(journal, ["findFeedback", "confirm", "markConfirmed"]);
  assert.deepEqual(calls.map((call) => call.name), ["markConfirmed"]);
});

test("an unconfirmable SUBMITTED_UNKNOWN job keeps its transaction and never resubmits", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const recordedRef: TransactionRef = {
    kind: "EVM",
    hash: TX,
    chainId: 84532,
    evidenceSource: "BASE_SEPOLIA_VERIFIED",
  };
  const staged = job({
    status: "SUBMITTED_UNKNOWN",
    feedbackHash: FEEDBACK_HASH,
    transactionRef: recordedRef,
  });
  const { outbox, calls } = fakeOutbox({ initial: staged, journal });
  const driven = await publisher({ ...chain, outbox, journal, confirm: () => null })
    .drive(staged);
  assert.equal(driven.status, "SUBMITTED_UNKNOWN");
  assert.equal(chain.giveFeedback.length, 0);
  const remark = calls.find((call) => call.name === "markSubmittedUnknown")?.args as {
    transactionRef: TransactionRef;
  };
  assert.deepEqual(remark.transactionRef, recordedRef);
  assert.deepEqual(journal, ["findFeedback", "confirm", "markSubmittedUnknown"]);
});

test("a broadcast without a decoded NewFeedback event is never CONFIRMED", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox, calls } = fakeOutbox({ initial: job(), journal });
  const driven = await publisher({ ...chain, outbox, journal, confirm: () => null })
    .publishClaimed();
  assert.equal(driven?.status, "SUBMITTED_UNKNOWN");
  assert.equal(chain.giveFeedback.length, 1);
  const staged = calls.find((call) => call.name === "markSubmittedUnknown")?.args as {
    transactionRef: TransactionRef;
    reason: string;
  };
  // The hash is kept as evidence of the broadcast, never as evidence of success.
  assert.deepEqual(staged.transactionRef, {
    kind: "EVM",
    hash: TX,
    chainId: 84532,
    evidenceSource: "BASE_SEPOLIA_VERIFIED",
  });
  assert.match(staged.reason, /without a confirmed NewFeedback event/);
  assert.equal(calls.some((call) => call.name === "markConfirmed"), false);
});

test("find before submit: existing on-chain feedback confirms with zero writes", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({
    journal,
    found: { transactionHash: TX, blockNumber: 900, logIndex: 2 },
  });
  const { outbox } = fakeOutbox({ initial: job(), journal });
  const driven = await publisher({ ...chain, outbox, journal, confirm: () => proof() })
    .publishClaimed();
  assert.equal(driven?.status, "CONFIRMED");
  assert.equal(chain.giveFeedback.length, 0);
  assert.deepEqual(journal, ["claim", "findFeedback", "confirm", "markConfirmed"]);
});

test("a recovered submission without a confirmed receipt is refused, not confirmed", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({
    journal,
    found: { transactionHash: TX, blockNumber: 900, logIndex: 2 },
  });
  const { outbox, calls } = fakeOutbox({ initial: job(), journal });
  await assert.rejects(
    () => publisher({ ...chain, outbox, journal, confirm: () => null }).publishClaimed(),
    /recovered reputation feedback has no confirmed receipt/,
  );
  assert.equal(calls.some((call) => call.name === "markConfirmed"), false);
});

test("a proof that does not match the job is refused", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox, calls } = fakeOutbox({ initial: job(), journal });
  await assert.rejects(
    () => publisher({
      ...chain,
      outbox,
      journal,
      confirm: () => proof({ feedbackHash: `0x${"ab".repeat(32)}` as Hex }),
    }).publishClaimed(),
    /confirmed feedback proof does not match the outbox job/,
  );
  assert.equal(calls.some((call) => call.name === "markConfirmed"), false);
});

test("a 409 on markPrepared is a policy failure with zero writes", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox } = fakeOutbox({ initial: job(), journal, conflictOn: "prepared" });
  await assert.rejects(
    () => publisher({ ...chain, outbox, journal, confirm: () => proof() }).publishClaimed(),
    (error: unknown) => {
      assert.ok(error instanceof Erc8004PolicyError);
      assert.match(error.message, /reputation outbox conflict/);
      return true;
    },
  );
  assert.equal(chain.giveFeedback.length, 0);
  assert.deepEqual(journal, ["claim", "findFeedback", "markPrepared"]);
});

test("a 409 on markConfirmed is a policy failure and is never retried", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox } = fakeOutbox({ initial: job(), journal, conflictOn: "confirmed" });
  await assert.rejects(
    () => publisher({ ...chain, outbox, journal, confirm: () => proof() }).publishClaimed(),
    Erc8004PolicyError,
  );
  assert.equal(chain.giveFeedback.length, 1);
  assert.equal(journal.filter((entry) => entry === "giveFeedback").length, 1);
});

test("an empty outbox yields no job and no chain traffic", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const outbox: ReputationOutboxApi = {
    async claim() { journal.push("claim"); return null; },
    async get() { journal.push("get"); return null; },
    async markPrepared() { throw new Error("unreachable"); },
    async markSubmittedUnknown() { throw new Error("unreachable"); },
    async markConfirmed() { throw new Error("unreachable"); },
  };
  const loop = publisher({ ...chain, outbox, journal, confirm: () => proof() });
  assert.equal(await loop.publishClaimed(), null);
  assert.equal(await loop.publishJob("job-missing"), null);
  assert.deepEqual(journal, ["claim", "get"]);
  assert.equal(chain.giveFeedback.length, 0);
});

test("a job for another registry is refused before any chain call", async () => {
  const journal: string[] = [];
  const chain = fakeContracts({ journal });
  const { outbox } = fakeOutbox({ initial: job(), journal });
  const loop = publisher({ ...chain, outbox, journal, confirm: () => proof() });
  await assert.rejects(
    () => loop.drive(job({ identity: { ...job().identity, chainId: 1 } })),
    /different registry/,
  );
  await assert.rejects(
    () => loop.drive(job({ identity: { ...job().identity, tag1: "other-tag" } })),
    /unknown feedback tags/,
  );
  await assert.rejects(
    () => loop.drive(job({ decision: { ...job().decision, value: 50 } })),
    /exactly 100 or 0/,
  );
  assert.deepEqual(journal, []);
});

test("query provenance carries coordinates and excludes untrusted or mistagged logs", async () => {
  const journal: string[] = [];
  const trustedEvent: RawFeedbackEvent = {
    value: 100,
    valueDecimals: 0,
    clientAddress: CLIENT.toLowerCase() as Address,
    blockNumber: 901,
    logIndex: 4,
    transactionRef: {
      kind: "EVM",
      hash: TX,
      chainId: 84532,
      evidenceSource: "BASE_SEPOLIA_VERIFIED",
      blockNumber: 901,
      logIndex: 4,
    },
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
  };
  const chain = fakeContracts({ journal, events: [trustedEvent] });
  const service = new Erc8004Service(chain.contracts, CLIENT);
  const result = await service.queryObjectiveFeedback({
    chainId: 84532,
    registryAddress: REGISTRY,
    erc8004AgentId: "1",
    trustedClients: [CLIENT],
    fromBlock: 800,
    toBlock: 1000,
  });
  assert.deepEqual(journal, ["queryFeedback"]);
  assert.equal(result.scope.tag1, FEEDBACK_TAG1);
  assert.equal(result.scope.tag2, FEEDBACK_TAG2);
  assert.equal(result.scope.erc8004AgentId, "1");
  assert.ok(Number.isFinite(Date.parse(result.queriedAt)));
  assert.equal(result.events.length, 1);
  const event = result.events[0];
  assert.ok(event !== undefined);
  assert.equal(event.blockNumber, 901);
  assert.equal(event.logIndex, 4);
  assert.equal(event.clientAddress, event.clientAddress.toLowerCase());
  assert.equal(event.transactionRef.kind, "EVM");

  // An empty trusted-client list can never aggregate, and the range must be ordered.
  await assert.rejects(
    () => service.queryObjectiveFeedback({
      chainId: 84532,
      registryAddress: REGISTRY,
      erc8004AgentId: "1",
      trustedClients: [],
      fromBlock: 800,
      toBlock: 1000,
    }),
    /must not be empty/,
  );
  await assert.rejects(
    () => service.queryObjectiveFeedback({
      chainId: 84532,
      registryAddress: REGISTRY,
      erc8004AgentId: "1",
      trustedClients: [CLIENT],
      fromBlock: 1000,
      toBlock: 800,
    }),
    /block range is invalid/,
  );
});

test("the viem log filter drops untrusted clients, wrong tags and coordinate-less logs", async () => {
  const logs = [
    {
      args: {
        agentId: 1n,
        clientAddress: CLIENT,
        value: 100n,
        valueDecimals: 0,
        tag1: FEEDBACK_TAG1,
        tag2: FEEDBACK_TAG2,
        feedbackHash: FEEDBACK_HASH,
        feedbackURI: AUDIT_BUNDLE,
      },
      transactionHash: TX,
      blockNumber: 901n,
      logIndex: 4,
    },
    {
      // Untrusted client: it can never enter an aggregation.
      args: {
        agentId: 1n,
        clientAddress: OUTSIDER,
        value: 100n,
        valueDecimals: 0,
        tag1: FEEDBACK_TAG1,
        tag2: FEEDBACK_TAG2,
        feedbackHash: FEEDBACK_HASH,
        feedbackURI: AUDIT_BUNDLE,
      },
      transactionHash: TX,
      blockNumber: 902n,
      logIndex: 5,
    },
    {
      // Wrong tag pair: a different claim entirely.
      args: {
        agentId: 1n,
        clientAddress: CLIENT,
        value: 100n,
        valueDecimals: 0,
        tag1: FEEDBACK_TAG1,
        tag2: "other-outcome",
        feedbackHash: FEEDBACK_HASH,
        feedbackURI: AUDIT_BUNDLE,
      },
      transactionHash: TX,
      blockNumber: 903n,
      logIndex: 6,
    },
    {
      // Pending log: coordinates are missing, so it is skipped instead of zero-filled.
      args: {
        agentId: 1n,
        clientAddress: CLIENT,
        value: 100n,
        valueDecimals: 0,
        tag1: FEEDBACK_TAG1,
        tag2: FEEDBACK_TAG2,
        feedbackHash: FEEDBACK_HASH,
        feedbackURI: AUDIT_BUNDLE,
      },
      transactionHash: TX,
      blockNumber: null,
      logIndex: null,
    },
  ];
  const publicClient = {
    async getBlockNumber() { return 1000n; },
    async getLogs() { return logs; },
    async readContract() { throw new Error("unreachable"); },
  };
  const contracts = new ViemErc8004Contracts({
    publicClient: publicClient as never,
    walletClient: { account: undefined } as never,
    identityRegistry: REGISTRY,
    reputationRegistry: REGISTRY,
  });
  const events = await contracts.queryFeedback({
    agentId: 1n,
    trustedClients: [CLIENT],
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
    fromBlock: 800n,
    toBlock: 1000n,
  });
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    value: 100,
    valueDecimals: 0,
    clientAddress: CLIENT.toLowerCase(),
    blockNumber: 901,
    logIndex: 4,
    transactionRef: {
      kind: "EVM",
      hash: TX,
      chainId: 84532,
      evidenceSource: "BASE_SEPOLIA_VERIFIED",
      blockNumber: 901,
      logIndex: 4,
    },
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
  });

  // The same filter governs recovery: a coordinate-less match is not a submission.
  const found = await contracts.findFeedback({
    agentId: 1n,
    clientAddress: CLIENT,
    value: 100n,
    valueDecimals: 0,
    tag1: FEEDBACK_TAG1,
    tag2: FEEDBACK_TAG2,
    feedbackHash: FEEDBACK_HASH,
  });
  assert.deepEqual(found, { transactionHash: TX, blockNumber: 901, logIndex: 4 });
});
