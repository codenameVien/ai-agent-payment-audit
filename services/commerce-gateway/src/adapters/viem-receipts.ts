import {
  decodeEventLog,
  parseAbi,
  type Address,
  type Hex,
  type PublicClient,
} from "viem";

import type { ReceiptProof, ReceiptReader } from "../contracts.js";

const TRANSFER_ABI = parseAbi([
  "event Transfer(address indexed from, address indexed to, uint256 value)",
]);

function safeNumber(value: bigint, label: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label} exceeds safe integer range`);
  return parsed;
}

export class ViemReceiptReader implements ReceiptReader {
  readonly #client: PublicClient;

  constructor(client: PublicClient) {
    this.#client = client;
  }

  async read(transactionHash: Hex): Promise<ReceiptProof | null> {
    let receipt;
    try {
      receipt = await this.#client.getTransactionReceipt({ hash: transactionHash });
    } catch (error) {
      const name = error instanceof Error ? error.name : "";
      if (name.includes("NotFound") || name.includes("TransactionReceipt")) return null;
      throw error;
    }
    const transfers: ReceiptProof["transfers"] = [];
    for (const log of receipt.logs) {
      try {
        const decoded = decodeEventLog({
          abi: TRANSFER_ABI,
          data: log.data,
          topics: log.topics,
          strict: true,
        });
        if (decoded.eventName !== "Transfer") continue;
        transfers.push({
          token: log.address as Address,
          from: decoded.args.from,
          to: decoded.args.to,
          amount: decoded.args.value,
          logIndex: log.logIndex,
        });
      } catch {
        continue;
      }
    }
    return {
      transactionHash: receipt.transactionHash,
      status: receipt.status === "success" ? 1 : 0,
      blockNumber: safeNumber(receipt.blockNumber, "block number"),
      transfers,
    };
  }
}
