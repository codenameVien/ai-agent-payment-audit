import { keccak256, stringToHex, type Address, type Hex } from "viem";

import type { EvidenceHash } from "./contracts.js";
import { digestToBytes32 } from "./digest.js";

export interface EvidenceHeadCheckpoint {
  purchaseId: string;
  eventCount: number;
  headEventHash: EvidenceHash;
}

export interface EvidenceAnchorApi {
  head(purchaseId: string): Promise<EvidenceHeadCheckpoint>;
  record(args: {
    checkpoint: EvidenceHeadCheckpoint;
    transactionHash: Hex;
    chainId: number;
    contractAddress: Address;
  }): Promise<void>;
}

export interface EvidenceAnchorContract {
  latest(purchaseIdHash: Hex): Promise<{ eventCount: bigint; headHash: Hex }>;
  findConfirmed(args: {
    purchaseIdHash: Hex;
    eventCount: bigint;
    headHash: Hex;
  }): Promise<Hex | null>;
  anchor(args: {
    purchaseIdHash: Hex;
    eventCount: bigint;
    previousHeadHash: Hex;
    headHash: Hex;
  }): Promise<{ transactionHash: Hex; confirmed: boolean }>;
}

export class EvidenceAnchorService {
  readonly #api: EvidenceAnchorApi;
  readonly #contract: EvidenceAnchorContract;
  readonly #chainId: number;
  readonly #contractAddress: Address;

  constructor(args: {
    api: EvidenceAnchorApi;
    contract: EvidenceAnchorContract;
    chainId: number;
    contractAddress: Address;
  }) {
    this.#api = args.api;
    this.#contract = args.contract;
    this.#chainId = args.chainId;
    this.#contractAddress = args.contractAddress;
  }

  async anchor(purchaseId: string): Promise<Hex> {
    const checkpoint = await this.#api.head(purchaseId);
    const purchaseIdHash = keccak256(stringToHex(purchaseId));
    const headHash = digestToBytes32(checkpoint.headEventHash);
    const previous = await this.#contract.latest(purchaseIdHash);
    if (
      BigInt(checkpoint.eventCount) === previous.eventCount &&
      headHash.toLowerCase() === previous.headHash.toLowerCase()
    ) {
      const transactionHash = await this.#contract.findConfirmed({
        purchaseIdHash,
        eventCount: BigInt(checkpoint.eventCount),
        headHash,
      });
      if (transactionHash === null) {
        throw new Error("matching on-chain anchor transaction was not found");
      }
      await this.#api.record({
        checkpoint,
        transactionHash,
        chainId: this.#chainId,
        contractAddress: this.#contractAddress,
      });
      return transactionHash;
    }
    if (BigInt(checkpoint.eventCount) <= previous.eventCount) {
      throw new Error("evidence checkpoint is not newer than the on-chain anchor");
    }
    const result = await this.#contract.anchor({
      purchaseIdHash,
      eventCount: BigInt(checkpoint.eventCount),
      previousHeadHash: previous.headHash,
      headHash,
    });
    if (!result.confirmed) {
      throw new Error("evidence anchor transaction was not confirmed exactly on-chain");
    }
    await this.#api.record({
      checkpoint,
      transactionHash: result.transactionHash,
      chainId: this.#chainId,
      contractAddress: this.#contractAddress,
    });
    return result.transactionHash;
  }
}
