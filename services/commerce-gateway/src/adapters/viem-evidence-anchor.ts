import {
  parseAbi,
  parseEventLogs,
  type Address,
  type Hex,
  type PublicClient,
  type WalletClient,
} from "viem";

import type { EvidenceAnchorContract } from "../evidence-anchor.js";
import { recentRpcLogFromBlock } from "./rpc-log-range.js";

export const EVIDENCE_ANCHOR_ABI = parseAbi([
  "function latest(bytes32 purchaseIdHash) view returns (uint64 eventCount, bytes32 headHash, uint64 anchoredAt)",
  "function anchor(bytes32 purchaseIdHash, uint64 eventCount, bytes32 previousHeadHash, bytes32 headHash)",
  "event EvidenceAnchored(bytes32 indexed purchaseIdHash, uint64 eventCount, bytes32 indexed previousHeadHash, bytes32 indexed headHash)",
]);

export class ViemEvidenceAnchorContract implements EvidenceAnchorContract {
  readonly #publicClient: PublicClient;
  readonly #walletClient: WalletClient;
  readonly #address: Address;

  constructor(args: {
    publicClient: PublicClient;
    walletClient: WalletClient;
    address: Address;
  }) {
    this.#publicClient = args.publicClient;
    this.#walletClient = args.walletClient;
    this.#address = args.address;
  }

  async latest(purchaseIdHash: Hex): Promise<{ eventCount: bigint; headHash: Hex }> {
    const [eventCount, headHash] = await this.#publicClient.readContract({
      address: this.#address,
      abi: EVIDENCE_ANCHOR_ABI,
      functionName: "latest",
      args: [purchaseIdHash],
    });
    return { eventCount, headHash };
  }

  async anchor(args: {
    purchaseIdHash: Hex;
    eventCount: bigint;
    previousHeadHash: Hex;
    headHash: Hex;
  }): Promise<{ transactionHash: Hex; confirmed: boolean }> {
    if (!this.#walletClient.account) throw new Error("anchor writer account is missing");
    const transactionHash = await this.#walletClient.writeContract({
      account: this.#walletClient.account,
      chain: this.#walletClient.chain,
      address: this.#address,
      abi: EVIDENCE_ANCHOR_ABI,
      functionName: "anchor",
      args: [
        args.purchaseIdHash,
        args.eventCount,
        args.previousHeadHash,
        args.headHash,
      ],
      gas: 200_000n,
    });
    const receipt = await this.#publicClient.waitForTransactionReceipt({
      hash: transactionHash,
    });
    if (receipt.status !== "success") {
      return { transactionHash, confirmed: false };
    }
    const events = parseEventLogs({
      abi: EVIDENCE_ANCHOR_ABI,
      logs: receipt.logs,
      eventName: "EvidenceAnchored",
      strict: true,
    });
    const confirmed = events.some((event) =>
      event.address.toLowerCase() === this.#address.toLowerCase() &&
      event.args.purchaseIdHash === args.purchaseIdHash &&
      event.args.eventCount === args.eventCount &&
      event.args.previousHeadHash === args.previousHeadHash &&
      event.args.headHash === args.headHash
    );
    return { transactionHash, confirmed };
  }

  async findConfirmed(args: {
    purchaseIdHash: Hex;
    eventCount: bigint;
    headHash: Hex;
  }): Promise<Hex | null> {
    const event = EVIDENCE_ANCHOR_ABI.find(
      (item) => item.type === "event" && item.name === "EvidenceAnchored",
    );
    if (event === undefined) throw new Error("EvidenceAnchored ABI is missing");
    const latestBlock = await this.#publicClient.getBlockNumber();
    const logs = await this.#publicClient.getLogs({
      address: this.#address,
      event,
      args: { purchaseIdHash: args.purchaseIdHash },
      fromBlock: recentRpcLogFromBlock(latestBlock),
      toBlock: latestBlock,
    });
    const match = logs.find((log) =>
      log.args.eventCount === args.eventCount &&
      log.args.headHash?.toLowerCase() === args.headHash.toLowerCase()
    );
    return match?.transactionHash ?? null;
  }
}
