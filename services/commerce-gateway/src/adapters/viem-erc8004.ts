import { createHash } from "node:crypto";

import { decodeEventLog, type Address, type Hex, type PublicClient, type WalletClient } from "viem";

import {
  BASE_SEPOLIA_CHAIN_ID,
  FEEDBACK_TAG1,
  FEEDBACK_TAG2,
  type ConfirmedFeedbackProof,
  type RawFeedbackEvent,
} from "../contracts.js";
import {
  ERC8004_IDENTITY_ABI,
  ERC8004_REPUTATION_ABI,
  type AgentIdentity,
  type Erc8004Contracts,
  type FeedbackSubmissionRef,
  type ReputationSummary,
  type ReputationReceiptConfirmer,
} from "../erc8004.js";
import { recentRpcLogFromBlock } from "./rpc-log-range.js";

const NEW_FEEDBACK_EVENT = ERC8004_REPUTATION_ABI.find(
  (item) => item.type === "event" && item.name === "NewFeedback",
);

export class ViemErc8004Contracts implements Erc8004Contracts {
  readonly #publicClient: PublicClient;
  readonly #walletClient: WalletClient;
  readonly #identityRegistry: Address;
  readonly #reputationRegistry: Address;

  constructor(args: {
    publicClient: PublicClient;
    walletClient: WalletClient;
    identityRegistry: Address;
    reputationRegistry: Address;
  }) {
    this.#publicClient = args.publicClient;
    this.#walletClient = args.walletClient;
    this.#identityRegistry = args.identityRegistry;
    this.#reputationRegistry = args.reputationRegistry;
  }

  async identity(agentId: bigint): Promise<AgentIdentity> {
    const [owner, agentWallet] = await Promise.all([
      this.#publicClient.readContract({
        address: this.#identityRegistry,
        abi: ERC8004_IDENTITY_ABI,
        functionName: "ownerOf",
        args: [agentId],
      }),
      this.#publicClient.readContract({
        address: this.#identityRegistry,
        abi: ERC8004_IDENTITY_ABI,
        functionName: "getAgentWallet",
        args: [agentId],
      }),
    ]);
    return { agentId, owner, agentWallet };
  }

  async summary(args: {
    agentId: bigint;
    trustedClients: readonly Address[];
    tag1: string;
    tag2: string;
  }): Promise<ReputationSummary> {
    const [count, value, valueDecimals] = await this.#publicClient.readContract({
      address: this.#reputationRegistry,
      abi: ERC8004_REPUTATION_ABI,
      functionName: "getSummary",
      args: [args.agentId, [...args.trustedClients], args.tag1, args.tag2],
    });
    return { count, value, valueDecimals };
  }

  async giveFeedback(args: {
    agentId: bigint;
    value: bigint;
    valueDecimals: number;
    tag1: string;
    tag2: string;
    endpoint: string;
    feedbackUri: string;
    feedbackHash: Hex;
  }): Promise<Hex> {
    if (!this.#walletClient.account) throw new Error("ERC-8004 writer account is missing");
    return this.#walletClient.writeContract({
      account: this.#walletClient.account,
      chain: this.#walletClient.chain,
      address: this.#reputationRegistry,
      abi: ERC8004_REPUTATION_ABI,
      functionName: "giveFeedback",
      args: [
        args.agentId,
        args.value,
        args.valueDecimals,
        args.tag1,
        args.tag2,
        args.endpoint,
        args.feedbackUri,
        args.feedbackHash,
      ],
      gas: 300_000n,
    });
  }

  async findFeedback(args: {
    agentId: bigint;
    clientAddress: Address;
    value: bigint;
    valueDecimals: number;
    tag1: string;
    tag2: string;
    feedbackHash: Hex;
  }): Promise<FeedbackSubmissionRef | null> {
    if (NEW_FEEDBACK_EVENT === undefined) throw new Error("NewFeedback ABI is missing");
    const latestBlock = await this.#publicClient.getBlockNumber();
    const logs = await this.#publicClient.getLogs({
      address: this.#reputationRegistry,
      event: NEW_FEEDBACK_EVENT,
      args: { agentId: args.agentId, clientAddress: args.clientAddress },
      fromBlock: recentRpcLogFromBlock(latestBlock),
      toBlock: latestBlock,
    });
    for (const log of logs) {
      if (
        log.args.value !== args.value ||
        log.args.valueDecimals !== args.valueDecimals ||
        log.args.tag1 !== args.tag1 ||
        log.args.tag2 !== args.tag2 ||
        log.args.feedbackHash?.toLowerCase() !== args.feedbackHash.toLowerCase()
      ) continue;
      // Provenance without coordinates is not provenance: an incomplete log is skipped
      // rather than completed with zeros.
      if (log.transactionHash === null || log.blockNumber === null || log.logIndex === null) {
        continue;
      }
      return {
        transactionHash: log.transactionHash,
        blockNumber: Number(log.blockNumber),
        logIndex: log.logIndex,
      };
    }
    return null;
  }

  async queryFeedback(args: {
    agentId: bigint;
    trustedClients: readonly Address[];
    tag1: string;
    tag2: string;
    fromBlock: bigint;
    toBlock: bigint;
  }): Promise<RawFeedbackEvent[]> {
    if (NEW_FEEDBACK_EVENT === undefined) throw new Error("NewFeedback ABI is missing");
    const trusted = new Set(args.trustedClients.map((client) => client.toLowerCase()));
    const logs = await this.#publicClient.getLogs({
      address: this.#reputationRegistry,
      event: NEW_FEEDBACK_EVENT,
      args: { agentId: args.agentId },
      fromBlock: args.fromBlock,
      toBlock: args.toBlock,
    });
    const events: RawFeedbackEvent[] = [];
    for (const log of logs) {
      const clientAddress = log.args.clientAddress;
      if (clientAddress === undefined || !trusted.has(clientAddress.toLowerCase())) continue;
      if (log.args.tag1 !== args.tag1 || log.args.tag2 !== args.tag2) continue;
      if (
        log.args.value === undefined ||
        log.args.valueDecimals === undefined ||
        log.transactionHash === null ||
        log.blockNumber === null ||
        log.logIndex === null
      ) continue;
      const blockNumber = Number(log.blockNumber);
      events.push({
        value: Number(log.args.value),
        valueDecimals: log.args.valueDecimals,
        clientAddress: clientAddress.toLowerCase() as Address,
        blockNumber,
        logIndex: log.logIndex,
        transactionRef: {
          kind: "EVM",
          hash: log.transactionHash,
          chainId: BASE_SEPOLIA_CHAIN_ID,
          evidenceSource: "BASE_SEPOLIA_VERIFIED",
          blockNumber,
          logIndex: log.logIndex,
        },
        tag1: log.args.tag1,
        tag2: log.args.tag2,
      });
    }
    return events;
  }
}

export class ViemReputationReceiptConfirmer implements ReputationReceiptConfirmer {
  readonly #publicClient: PublicClient;

  constructor(publicClient: PublicClient) {
    this.#publicClient = publicClient;
  }

  async confirm(args: {
    transactionHash: Hex;
    registryAddress: Address;
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    evidenceSource: "BASE_SEPOLIA_VERIFIED" | "HISTORICAL_ON_CHAIN";
  }): Promise<ConfirmedFeedbackProof | null> {
    const receipt = await this.#publicClient.waitForTransactionReceipt({
      hash: args.transactionHash,
    });
    if (
      receipt.status !== "success" ||
      receipt.to?.toLowerCase() !== args.registryAddress.toLowerCase()
    ) return null;
    for (const log of receipt.logs) {
      if (log.address.toLowerCase() !== args.registryAddress.toLowerCase()) continue;
      let decoded;
      try {
        decoded = decodeEventLog({
          abi: ERC8004_REPUTATION_ABI,
          eventName: "NewFeedback",
          topics: log.topics,
          data: log.data,
        });
      } catch {
        continue;
      }
      if (
        decoded.args.agentId !== args.agentId ||
        decoded.args.value !== BigInt(args.objectiveValue) ||
        decoded.args.valueDecimals !== 0 ||
        decoded.args.tag1 !== FEEDBACK_TAG1 ||
        decoded.args.tag2 !== FEEDBACK_TAG2 ||
        decoded.args.feedbackHash.toLowerCase() !== args.feedbackHash.toLowerCase()
      ) continue;
      if (log.blockNumber === null || log.logIndex === null) continue;
      const blockNumber = Number(log.blockNumber);
      const receiptProofRef = `sha256:${createHash("sha256")
        .update(`${args.transactionHash}:${log.logIndex}:feedback`)
        .digest("hex")}`;
      return {
        transactionRef: {
          kind: "EVM",
          hash: args.transactionHash,
          chainId: BASE_SEPOLIA_CHAIN_ID,
          evidenceSource: args.evidenceSource,
          blockNumber,
          logIndex: log.logIndex,
        },
        receiptProofRef,
        clientAddress: decoded.args.clientAddress.toLowerCase() as Address,
        erc8004AgentId: decoded.args.agentId.toString(),
        value: args.objectiveValue,
        valueDecimals: 0,
        feedbackHash: decoded.args.feedbackHash,
        blockNumber,
        logIndex: log.logIndex,
        tag1: decoded.args.tag1,
        tag2: decoded.args.tag2,
        feedbackUri: decoded.args.feedbackURI,
      };
    }
    return null;
  }
}
