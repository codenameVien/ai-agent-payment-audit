import { decodeEventLog, type Address, type Hex, type PublicClient, type WalletClient } from "viem";

import {
  ERC8004_IDENTITY_ABI,
  ERC8004_REPUTATION_ABI,
  type AgentIdentity,
  type Erc8004Contracts,
  type ReputationSummary,
  type ReputationReceiptConfirmer,
} from "../erc8004.js";
import { recentRpcLogFromBlock } from "./rpc-log-range.js";

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
  }): Promise<Hex | null> {
    const event = ERC8004_REPUTATION_ABI.find(
      (item) => item.type === "event" && item.name === "NewFeedback",
    );
    if (event === undefined) throw new Error("NewFeedback ABI is missing");
    const latestBlock = await this.#publicClient.getBlockNumber();
    const logs = await this.#publicClient.getLogs({
      address: this.#reputationRegistry,
      event,
      args: { agentId: args.agentId, clientAddress: args.clientAddress },
      fromBlock: recentRpcLogFromBlock(latestBlock),
      toBlock: latestBlock,
    });
    const match = logs.find((log) =>
      log.args.value === args.value &&
      log.args.valueDecimals === args.valueDecimals &&
      log.args.tag1 === args.tag1 &&
      log.args.tag2 === args.tag2 &&
      log.args.feedbackHash?.toLowerCase() === args.feedbackHash.toLowerCase()
    );
    return match?.transactionHash ?? null;
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
  }): Promise<boolean> {
    const receipt = await this.#publicClient.waitForTransactionReceipt({
      hash: args.transactionHash,
    });
    if (
      receipt.status !== "success" ||
      receipt.to?.toLowerCase() !== args.registryAddress.toLowerCase()
    ) return false;
    return receipt.logs.some((log) => {
      if (log.address.toLowerCase() !== args.registryAddress.toLowerCase()) return false;
      try {
        const decoded = decodeEventLog({
          abi: ERC8004_REPUTATION_ABI,
          eventName: "NewFeedback",
          topics: log.topics,
          data: log.data,
        });
        return (
          decoded.args.agentId === args.agentId &&
          decoded.args.value === BigInt(args.objectiveValue) &&
          decoded.args.valueDecimals === 0 &&
          decoded.args.tag1 === "pbl-audit" &&
          decoded.args.tag2 === "payment-outcome" &&
          decoded.args.feedbackHash.toLowerCase() === args.feedbackHash.toLowerCase()
        );
      } catch {
        return false;
      }
    });
  }
}
