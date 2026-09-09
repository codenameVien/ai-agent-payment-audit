import { createHash } from "node:crypto";

import { keccak256, parseAbi, stringToHex, type Address, type Hex } from "viem";

import {
  BASE_SEPOLIA_CHAIN_ID,
  FEEDBACK_TAG1,
  FEEDBACK_TAG2,
  type ConfirmedFeedbackProof,
  type FeedbackQueryResult,
  type RawFeedbackEvent,
  type ReputationOutboxApi,
  type ReputationPublishJob,
  type TransactionRef,
} from "./contracts.js";

export interface AgentIdentity {
  agentId: bigint;
  owner: Address;
  agentWallet: Address;
}

export interface ReputationSummary {
  count: bigint;
  value: bigint;
  valueDecimals: number;
}

/** Where a `NewFeedback` event actually lives. A bare hash loses the coordinates. */
export interface FeedbackSubmissionRef {
  transactionHash: Hex;
  blockNumber: number;
  logIndex: number;
}

/**
 * Turns a transaction reference into a proof, or into nothing. It returns `null`
 * whenever the receipt or the decoded `NewFeedback` event does not exist, so a
 * transaction-shaped string alone can never be read as a publication.
 */
export interface ReputationReceiptConfirmer {
  confirm(args: {
    transactionHash: Hex;
    registryAddress: Address;
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    evidenceSource: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
  }): Promise<ConfirmedFeedbackProof | null>;
}

export interface Erc8004Contracts {
  identity(agentId: bigint): Promise<AgentIdentity>;
  summary(args: {
    agentId: bigint;
    trustedClients: readonly Address[];
    tag1: string;
    tag2: string;
  }): Promise<ReputationSummary>;
  giveFeedback(args: {
    agentId: bigint;
    value: bigint;
    valueDecimals: number;
    tag1: string;
    tag2: string;
    endpoint: string;
    feedbackUri: string;
    feedbackHash: Hex;
  }): Promise<Hex>;
  findFeedback(args: {
    agentId: bigint;
    clientAddress: Address;
    value: bigint;
    valueDecimals: number;
    tag1: string;
    tag2: string;
    feedbackHash: Hex;
  }): Promise<FeedbackSubmissionRef | null>;
  queryFeedback(args: {
    agentId: bigint;
    trustedClients: readonly Address[];
    tag1: string;
    tag2: string;
    fromBlock: bigint;
    toBlock: bigint;
  }): Promise<RawFeedbackEvent[]>;
  /** The current chain head. Read-only, so it is safe in any write mode. */
  latestBlock(): Promise<bigint>;
}

export class Erc8004PolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Erc8004PolicyError";
  }
}

/**
 * A durable-state rejection. `reason` says whether it was ordinary contention - a lease
 * that moved, a status somebody else already advanced - or a genuine immutable payload
 * conflict. Only `PAYLOAD_FINGERPRINT_MISMATCH` may ever produce conflict evidence.
 */
export type OutboxConflictReason =
  | "PAYLOAD_FINGERPRINT_MISMATCH"
  | "LEASE_MOVED"
  | "STALE_STATUS"
  | "ALREADY_TERMINAL"
  | "TRANSACTION_ALREADY_BOUND"
  | "DIFFERENT_CONFIRMATION"
  | "PREPARED_COMMITMENT_CHANGED"
  | "SAME_FINGERPRINT_NOT_A_CONFLICT"
  | "UNSPECIFIED";

export class ReputationOutboxConflictError extends Error {
  readonly reason: OutboxConflictReason;

  constructor(message: string, reason: OutboxConflictReason = "UNSPECIFIED") {
    super(message);
    this.name = "ReputationOutboxConflictError";
    this.reason = reason;
  }
}

/** A read window wider than this is a chain scan, not a query. */
const MAX_FEEDBACK_LOOKBACK_BLOCKS = 100_000;

export class Erc8004Service {
  readonly #contracts: Erc8004Contracts;
  readonly #clientAddress: Address;

  constructor(contracts: Erc8004Contracts, clientAddress: Address) {
    this.#contracts = contracts;
    this.#clientAddress = clientAddress;
  }

  /** The address this service writes feedback from. Never overridable by a caller. */
  get clientAddress(): Address {
    return this.#clientAddress;
  }

  async verifyQuoteSigner(agentId: bigint, signer: Address): Promise<AgentIdentity> {
    const identity = await this.#contracts.identity(agentId);
    if (identity.agentWallet.toLowerCase() !== signer.toLowerCase()) {
      throw new Erc8004PolicyError("quote signer is not the ERC-8004 agent wallet");
    }
    return identity;
  }

  summary(
    agentId: bigint,
    trustedClients: readonly Address[],
    tag1 = FEEDBACK_TAG1,
    tag2 = FEEDBACK_TAG2,
  ): Promise<ReputationSummary> {
    if (trustedClients.length === 0) {
      throw new Erc8004PolicyError("trusted client list must not be empty");
    }
    return this.#contracts.summary({ agentId, trustedClients, tag1, tag2 });
  }

  async submitObjectiveFeedback(args: {
    agentId: bigint;
    verifiedSuccess: boolean;
    endpoint?: string;
    feedbackUri?: string;
    feedbackHash: Hex;
  }): Promise<Hex> {
    const identity = await this.#contracts.identity(args.agentId);
    const client = this.#clientAddress.toLowerCase();
    if (
      client === identity.owner.toLowerCase() ||
      client === identity.agentWallet.toLowerCase()
    ) {
      throw new Erc8004PolicyError("self-feedback is forbidden");
    }
    return this.#contracts.giveFeedback({
      agentId: args.agentId,
      value: args.verifiedSuccess ? 100n : 0n,
      valueDecimals: 0,
      tag1: FEEDBACK_TAG1,
      tag2: FEEDBACK_TAG2,
      endpoint: args.endpoint ?? "",
      feedbackUri: args.feedbackUri ?? "",
      feedbackHash: args.feedbackHash,
    });
  }

  findObjectiveFeedback(args: {
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
  }): Promise<FeedbackSubmissionRef | null> {
    return this.#contracts.findFeedback({
      agentId: args.agentId,
      clientAddress: this.#clientAddress,
      value: BigInt(args.objectiveValue),
      valueDecimals: 0,
      tag1: FEEDBACK_TAG1,
      tag2: FEEDBACK_TAG2,
      feedbackHash: args.feedbackHash,
    });
  }

  /** Read-only aggregation input. It never writes, so it is safe in any write mode. */
  async queryObjectiveFeedback(args: {
    chainId: number;
    registryAddress: Address;
    erc8004AgentId: string;
    trustedClients: readonly Address[];
    /** How many blocks back from the head to read. The caller never names the window. */
    lookbackBlocks: number;
  }): Promise<FeedbackQueryResult> {
    if (args.trustedClients.length === 0) {
      throw new Erc8004PolicyError("trusted client list must not be empty");
    }
    if (
      !Number.isSafeInteger(args.lookbackBlocks) ||
      args.lookbackBlocks < 1 ||
      args.lookbackBlocks > MAX_FEEDBACK_LOOKBACK_BLOCKS
    ) {
      throw new Erc8004PolicyError(
        `feedback query lookbackBlocks must be an integer in 1..${MAX_FEEDBACK_LOOKBACK_BLOCKS}`,
      );
    }
    const agentId = BigInt(args.erc8004AgentId);
    // The range is a bounded suffix of the head resolved here, so a caller can neither
    // pin a stale window nor ask for a full-chain scan.
    const head = await this.#contracts.latestBlock();
    const toBlock = Number(head);
    if (!Number.isSafeInteger(toBlock) || toBlock < 0) {
      throw new Erc8004PolicyError("chain head is not a usable block number");
    }
    const fromBlock = Math.max(0, toBlock - args.lookbackBlocks + 1);
    const events = await this.#contracts.queryFeedback({
      agentId,
      trustedClients: args.trustedClients,
      tag1: FEEDBACK_TAG1,
      tag2: FEEDBACK_TAG2,
      fromBlock: BigInt(fromBlock),
      toBlock: BigInt(toBlock),
    });
    return {
      scope: {
        chainId: args.chainId,
        registryAddress: args.registryAddress,
        erc8004AgentId: agentId.toString(),
        trustedClients: args.trustedClients,
        tag1: FEEDBACK_TAG1,
        tag2: FEEDBACK_TAG2,
        fromBlock,
        toBlock,
      },
      latestBlock: toBlock,
      queriedAt: new Date().toISOString(),
      events,
    };
  }
}

const DEFAULT_LEASE_SECONDS = 60;

/** Every durable-state refusal this loop can lose is the same kind of race. */
const OUTBOX_CONFLICT_REASON_CODE = "OUTBOX_TRANSITION_CONFLICT";

/**
 * There is deliberately no pre-broadcast transaction reference. Inventing a
 * `SYNTHETIC_LOCAL` identifier for a live submission would fabricate provenance, so
 * `PREPARED` records only the immutable feedback-hash commitment and the reference is
 * bound exactly once, when the submission actually exists.
 */

function objectiveValueOf(value: number | null): 0 | 100 {
  if (value === 100) return 100;
  if (value === 0) return 0;
  throw new Erc8004PolicyError("objective feedback value must be exactly 100 or 0");
}

/**
 * Drives one durable outbox job to a terminal state. It holds no publication state of
 * its own: the lease, the attempt count and the recorded transaction all live in the
 * Evidence API, so a crash between broadcast and confirmation is recoverable and a
 * second process can never produce a second write.
 */
export class Erc8004ReputationPublisher {
  readonly #service: Erc8004Service;
  readonly #outbox: ReputationOutboxApi;
  readonly #receipts: ReputationReceiptConfirmer;
  readonly #chainId: number;
  readonly #registryAddress: Address;
  readonly #workerId: string;
  readonly #leaseSeconds: number;
  readonly #writeMode: "disabled" | "live";

  constructor(args: {
    service: Erc8004Service;
    outbox: ReputationOutboxApi;
    receipts: ReputationReceiptConfirmer;
    chainId: number;
    registryAddress: Address;
    workerId: string;
    leaseSeconds?: number;
    writeMode?: "disabled" | "live";
  }) {
    this.#service = args.service;
    this.#outbox = args.outbox;
    this.#receipts = args.receipts;
    this.#chainId = args.chainId;
    this.#registryAddress = args.registryAddress;
    this.#workerId = args.workerId;
    this.#leaseSeconds = args.leaseSeconds ?? DEFAULT_LEASE_SECONDS;
    this.#writeMode = args.writeMode ?? "disabled";
  }

  async publishClaimed(): Promise<ReputationPublishJob | null> {
    const job = await this.#outbox.claim({
      workerId: this.#workerId,
      leaseSeconds: this.#leaseSeconds,
    });
    if (job === null) return null;
    return this.drive(job);
  }

  async publishJob(jobId: string): Promise<ReputationPublishJob | null> {
    const job = await this.#outbox.get(jobId);
    if (job === null) return null;
    return this.drive(job);
  }

  async drive(job: ReputationPublishJob): Promise<ReputationPublishJob> {
    if (job.status === "CONFIRMED" || job.status === "DEFERRED" || job.status === "CONFLICT") {
      return job;
    }
    if (job.decision.decision === "DEFER") return job;
    if (
      job.identity.chainId !== this.#chainId ||
      job.identity.registryAddress.toLowerCase() !== this.#registryAddress.toLowerCase()
    ) {
      throw new Erc8004PolicyError("reputation job targets a different registry");
    }
    if (job.identity.tag1 !== FEEDBACK_TAG1 || job.identity.tag2 !== FEEDBACK_TAG2) {
      throw new Erc8004PolicyError("reputation job carries unknown feedback tags");
    }
    // The status observed on entry decides whether a broadcast is still permitted. A job
    // that was already PREPARED or SUBMITTED_UNKNOWN before this attempt began carries a
    // durable commitment, so preparing it again would authorise a second publication.
    const startingStatus = job.status;
    const agentId = BigInt(job.decision.erc8004AgentId);
    const objectiveValue = objectiveValueOf(job.decision.value);
    const feedbackHash = job.feedbackHash ?? keccak256(stringToHex(job.payloadFingerprint));

    // Find before submit, from every non-terminal state: an already published effect is
    // recovered instead of duplicated.
    const found = await this.#service.findObjectiveFeedback({
      agentId,
      objectiveValue,
      feedbackHash,
    });
    if (found !== null) {
      const proof = await this.#receipts.confirm({
        transactionHash: found.transactionHash,
        registryAddress: this.#registryAddress,
        agentId,
        objectiveValue,
        feedbackHash,
        evidenceSource: "HISTORICAL_ON_CHAIN",
      });
      if (proof === null) {
        throw new Erc8004PolicyError("recovered reputation feedback has no confirmed receipt");
      }
      return this.#confirm(job, proof, { agentId, objectiveValue, feedbackHash });
    }

    if (this.#writeMode !== "live") {
      throw new Erc8004PolicyError("reputation writer is disabled");
    }

    const recorded = job.transactionRef;
    if (recorded !== null && recorded !== undefined) {
      // A submission identity is already bound. Resolving it can never mean building a
      // second transaction, so `giveFeedback` is unreachable from here.
      if (recorded.kind === "EVM") {
        const proof = await this.#receipts.confirm({
          transactionHash: recorded.hash,
          registryAddress: this.#registryAddress,
          agentId,
          objectiveValue,
          feedbackHash,
          evidenceSource: recorded.evidenceSource,
        });
        if (proof !== null) {
          return this.#confirm(job, proof, { agentId, objectiveValue, feedbackHash });
        }
      }
      return this.#submittedUnknown(
        job,
        "recorded reputation transaction is not confirmable yet",
        recorded,
      );
    }

    if (startingStatus !== "PENDING" && startingStatus !== "LEASED") {
      // Committed before this attempt, no reference bound, and nothing matching on chain:
      // an external effect may exist that cannot be named. Fail closed into reconciliation
      // rather than broadcast a transaction the durable record already promised.
      return this.#submittedUnknown(
        job,
        `recorded reputation intent is unresolved from ${startingStatus}: no transaction can be named`,
      );
    }

    // PENDING or LEASED: nothing has been committed yet, so this attempt owns the single
    // broadcast. Persist the whole commitment first so a crash after it stays discoverable.
    const prepared = await this.#guard(job, () =>
      this.#outbox.markPrepared({
        jobId: job.jobId,
        workerId: this.#workerId,
        payloadFingerprint: job.payloadFingerprint,
        feedbackHash,
        clientAddress: this.#service.clientAddress,
        feedbackUri: job.decision.auditBundleHash,
      }),
    );
    const transactionHash = await this.#service.submitObjectiveFeedback({
      agentId,
      verifiedSuccess: objectiveValue === 100,
      feedbackUri: job.decision.auditBundleHash,
      feedbackHash,
    });
    const proof = await this.#receipts.confirm({
      transactionHash,
      registryAddress: this.#registryAddress,
      agentId,
      objectiveValue,
      feedbackHash,
      evidenceSource: "BASE_SEPOLIA_VERIFIED",
    });
    if (proof === null) {
      return this.#submittedUnknown(
        prepared,
        "reputation transaction was broadcast without a confirmed NewFeedback event",
        {
          kind: "EVM",
          hash: transactionHash,
          chainId: BASE_SEPOLIA_CHAIN_ID,
          evidenceSource: "BASE_SEPOLIA_VERIFIED",
        },
      );
    }
    return this.#confirm(prepared, proof, { agentId, objectiveValue, feedbackHash });
  }

  /**
   * A proof is accepted only when it matches the entire commitment: the agent, the value,
   * the hash, the tags, the registry it was read from, the client that wrote it and the
   * audit bundle it points at. A proof that matches only some of those is a different fact.
   */
  #confirm(
    job: ReputationPublishJob,
    proof: ConfirmedFeedbackProof,
    expected: { agentId: bigint; objectiveValue: 0 | 100; feedbackHash: Hex },
  ): Promise<ReputationPublishJob> {
    if (
      BigInt(proof.erc8004AgentId) !== expected.agentId ||
      proof.value !== expected.objectiveValue ||
      proof.feedbackHash.toLowerCase() !== expected.feedbackHash.toLowerCase() ||
      proof.tag1 !== FEEDBACK_TAG1 ||
      proof.tag2 !== FEEDBACK_TAG2 ||
      proof.registryAddress.toLowerCase() !== this.#registryAddress.toLowerCase() ||
      proof.clientAddress.toLowerCase() !== this.#service.clientAddress.toLowerCase() ||
      proof.feedbackUri !== job.decision.auditBundleHash ||
      (job.feedbackHash !== null &&
        proof.feedbackHash.toLowerCase() !== job.feedbackHash.toLowerCase())
    ) {
      throw new Erc8004PolicyError("confirmed feedback proof does not match the outbox job");
    }
    return this.#guard(job, () =>
      this.#outbox.markConfirmed({
        jobId: job.jobId,
        workerId: this.#workerId,
        payloadFingerprint: job.payloadFingerprint,
        proof,
      }),
    );
  }

  #submittedUnknown(
    job: ReputationPublishJob,
    reason: string,
    transactionRef?: TransactionRef,
  ): Promise<ReputationPublishJob> {
    return this.#guard(job, () =>
      this.#outbox.markSubmittedUnknown({
        jobId: job.jobId,
        workerId: this.#workerId,
        payloadFingerprint: job.payloadFingerprint,
        reason,
        ...(transactionRef === undefined ? {} : { transactionRef }),
      }),
    );
  }

  /**
   * A durable-state conflict is terminal for this attempt: never retried with a new write.
   *
   * Only an immutable payload-fingerprint mismatch is a publication conflict. Losing a
   * lease, or arriving at a status another worker already advanced, is ordinary
   * contention: another worker continues the job, so requesting conflict evidence would
   * terminalize a still-valid job and could leave a real external effect with no
   * `REPUTATION_RECORDED`.
   */
  async #guard<T>(job: ReputationPublishJob, operation: () => Promise<T>): Promise<T> {
    try {
      return await operation();
    } catch (error) {
      if (error instanceof ReputationOutboxConflictError) {
        if (error.reason === "PAYLOAD_FINGERPRINT_MISMATCH") {
          // The losing payload is itself evidence, so it is recorded against the publish
          // identity. That write must never be able to hide the conflict that caused it.
          try {
            await this.#outbox.recordConflict({
              identityHash: job.identityHash,
              requestedFingerprint: job.payloadFingerprint,
              reasonCode: OUTBOX_CONFLICT_REASON_CODE,
            });
          } catch {
            // Deliberately swallowed: the original conflict is the error that matters.
          }
        }
        throw new Erc8004PolicyError(
          `reputation outbox conflict (${error.reason}): ${error.message}`,
        );
      }
      throw error;
    }
  }
}

export const ERC8004_IDENTITY_ABI = parseAbi([
  "function ownerOf(uint256 agentId) view returns (address)",
  "function getAgentWallet(uint256 agentId) view returns (address)",
]);

export const ERC8004_REPUTATION_ABI = parseAbi([
  "event NewFeedback(uint256 indexed agentId, address indexed clientAddress, uint64 feedbackIndex, int128 value, uint8 valueDecimals, string indexed indexedTag1, string tag1, string tag2, string endpoint, string feedbackURI, bytes32 feedbackHash)",
  "function getSummary(uint256 agentId, address[] clientAddresses, string tag1, string tag2) view returns (uint64 count, int128 summaryValue, uint8 summaryValueDecimals)",
  "function giveFeedback(uint256 agentId, int128 value, uint8 valueDecimals, string tag1, string tag2, string endpoint, string feedbackURI, bytes32 feedbackHash)",
]);

export const BASE_SEPOLIA_ERC8004_IDENTITY =
  "0x8004A818BFB912233c491871b3d84c89A494BD9e" as const;
export const BASE_SEPOLIA_ERC8004_REPUTATION =
  "0x8004B663056A597Dffe9eCcC1965A193B7388713" as const;
