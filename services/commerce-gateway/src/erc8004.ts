import { parseAbi, type Address, type Hex } from "viem";

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

export interface ReputationEvidenceApi {
  prepare(purchaseId: string, agentId: bigint): Promise<{
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    transactionHash?: Hex;
  }>;
  record(args: {
    purchaseId: string;
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
    transactionHash: Hex;
    chainId: number;
    registryAddress: Address;
  }): Promise<void>;
}

export interface ReputationReceiptConfirmer {
  confirm(args: {
    transactionHash: Hex;
    registryAddress: Address;
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
  }): Promise<boolean>;
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
  }): Promise<Hex | null>;
}

export class Erc8004PolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "Erc8004PolicyError";
  }
}

export class Erc8004Service {
  readonly #contracts: Erc8004Contracts;
  readonly #clientAddress: Address;

  constructor(contracts: Erc8004Contracts, clientAddress: Address) {
    this.#contracts = contracts;
    this.#clientAddress = clientAddress;
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
    tag1 = "pbl-audit",
    tag2 = "payment-outcome",
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
      tag1: "pbl-audit",
      tag2: "payment-outcome",
      endpoint: args.endpoint ?? "",
      feedbackUri: args.feedbackUri ?? "",
      feedbackHash: args.feedbackHash,
    });
  }

  findObjectiveFeedback(args: {
    agentId: bigint;
    objectiveValue: 0 | 100;
    feedbackHash: Hex;
  }): Promise<Hex | null> {
    return this.#contracts.findFeedback({
      agentId: args.agentId,
      clientAddress: this.#clientAddress,
      value: BigInt(args.objectiveValue),
      valueDecimals: 0,
      tag1: "pbl-audit",
      tag2: "payment-outcome",
      feedbackHash: args.feedbackHash,
    });
  }
}

export class Erc8004ReputationPublisher {
  readonly #service: Erc8004Service;
  readonly #evidence: ReputationEvidenceApi;
  readonly #receipts: ReputationReceiptConfirmer;
  readonly #chainId: number;
  readonly #registryAddress: Address;
  readonly #publishing = new Map<string, Promise<Hex>>();

  constructor(args: {
    service: Erc8004Service;
    evidence: ReputationEvidenceApi;
    receipts: ReputationReceiptConfirmer;
    chainId: number;
    registryAddress: Address;
  }) {
    this.#service = args.service;
    this.#evidence = args.evidence;
    this.#receipts = args.receipts;
    this.#chainId = args.chainId;
    this.#registryAddress = args.registryAddress;
  }

  async publish(args: {
    purchaseId: string;
    agentId: bigint;
    endpoint?: string;
    feedbackUri?: string;
  }): Promise<Hex> {
    const key = `${args.purchaseId}:${args.agentId}`;
    const existing = this.#publishing.get(key);
    if (existing !== undefined) return existing;
    const promise = this.#publishOnce(args);
    this.#publishing.set(key, promise);
    try {
      return await promise;
    } catch (error) {
      this.#publishing.delete(key);
      throw error;
    }
  }

  async #publishOnce(args: {
    purchaseId: string;
    agentId: bigint;
    endpoint?: string;
    feedbackUri?: string;
  }): Promise<Hex> {
    const intent = await this.#evidence.prepare(args.purchaseId, args.agentId);
    if (intent.agentId !== args.agentId) {
      throw new Erc8004PolicyError("reputation intent agent mismatch");
    }
    if (intent.transactionHash !== undefined) return intent.transactionHash;
    const recovered = await this.#service.findObjectiveFeedback({
      agentId: args.agentId,
      objectiveValue: intent.objectiveValue,
      feedbackHash: intent.feedbackHash,
    });
    const transactionHash = recovered ?? await this.#service.submitObjectiveFeedback({
      agentId: args.agentId,
      verifiedSuccess: intent.objectiveValue === 100,
      endpoint: args.endpoint,
      feedbackUri: args.feedbackUri,
      feedbackHash: intent.feedbackHash,
    });
    if (!(await this.#receipts.confirm({
      transactionHash,
      registryAddress: this.#registryAddress,
      agentId: args.agentId,
      objectiveValue: intent.objectiveValue,
      feedbackHash: intent.feedbackHash,
    }))) {
      throw new Erc8004PolicyError("reputation transaction was not confirmed");
    }
    await this.#evidence.record({
      purchaseId: args.purchaseId,
      agentId: args.agentId,
      objectiveValue: intent.objectiveValue,
      feedbackHash: intent.feedbackHash,
      transactionHash,
      chainId: this.#chainId,
      registryAddress: this.#registryAddress,
    });
    return transactionHash;
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
