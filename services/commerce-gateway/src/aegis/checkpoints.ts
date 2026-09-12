import { createPublicClient, createWalletClient, http, isAddress, keccak256, stringToHex, type Address, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

import { ViemEvidenceAnchorContract } from "../adapters/viem-evidence-anchor.js";
import { EvidenceAnchorService, type EvidenceAnchorContract } from "../evidence-anchor.js";

export type CheckpointPhase = "decision" | "audit";
export type CheckpointMode = "off" | "mock" | "live";
export interface CheckpointRecord {
  mode: "mock" | "live";
  eventCount: number;
  headEventHash: string;
  transactionHash?: Hex;
  chainId?: number;
  contractAddress?: Address;
}
interface CheckpointView {
  purchaseId: string;
  phase: CheckpointPhase;
  eventCount: number;
  headEventHash: string;
  record?: CheckpointRecord | null;
}

/** The signer reads only verified checkpoints from the Evidence API, never MongoDB. */
export class AegisCheckpoints {
  readonly mode: CheckpointMode;
  readonly #base: string;
  readonly #token: string;
  readonly #fetch: typeof fetch;
  readonly #contract?: EvidenceAnchorContract;
  readonly #address?: Address;
  readonly #pending = new Map<string, Promise<CheckpointRecord>>();

  constructor(args: { mode: CheckpointMode; baseUrl: string; internalToken: string;
    fetchImpl?: typeof fetch; contract?: EvidenceAnchorContract; contractAddress?: Address }) {
    this.mode = args.mode;
    this.#base = args.baseUrl.replace(/\/$/, "");
    this.#token = args.internalToken;
    this.#fetch = args.fetchImpl ?? fetch;
    this.#contract = args.contract;
    this.#address = args.contractAddress;
  }

  async #api(purchaseId: string, phase: CheckpointPhase, body?: CheckpointRecord): Promise<CheckpointView> {
    const response = await this.#fetch(`${this.#base}/internal/evidence/purchases/${encodeURIComponent(purchaseId)}/checkpoints/${phase}`, {
      method: body ? "POST" : "GET",
      headers: { authorization: `Bearer ${this.#token}`, "content-type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) throw new Error(`checkpoint evidence API HTTP ${response.status}`);
    const result = await response.json() as CheckpointView;
    if (body) return result;
    if (result.purchaseId !== purchaseId || result.phase !== phase ||
        !Number.isSafeInteger(result.eventCount) || result.eventCount <= 0 ||
        !/^sha256:[0-9a-f]{64}$/.test(result.headEventHash)) {
      throw new Error("invalid verified checkpoint");
    }
    return result;
  }

  async requireDecision(purchaseId: string): Promise<void> {
    if (this.mode === "off") return;
    const view = await this.#api(purchaseId, "decision");
    const record = this.#validatedRecord(view);
    if (!record) throw new Error("decision checkpoint required before payment");
    await this.#verifyRecorded(view, record);
  }

  async #verifyRecorded(view: CheckpointView, record: CheckpointRecord): Promise<void> {
    if (record.mode !== "live") return;
    if (!this.#contract) throw new Error("live anchor verifier unavailable");
    // Recomputed Mongo hashes are not sufficient: the exact committed prefix must
    // still match the external checkpoint, including on replay and before audit.
    const confirmed = await this.#contract.findConfirmed({
      purchaseIdHash: keccak256(stringToHex(view.purchaseId)),
      eventCount: BigInt(view.eventCount), headHash: `0x${view.headEventHash.slice(7)}` as Hex,
    });
    if (!confirmed || confirmed.toLowerCase() !== record.transactionHash?.toLowerCase()) {
      throw new Error("stored evidence differs from confirmed on-chain checkpoint or proof unavailable");
    }
  }

  #validatedRecord(view: CheckpointView): CheckpointRecord | null {
    const record = view.record;
    if (!record) return null;
    if (record.mode !== this.mode || record.eventCount !== view.eventCount || record.headEventHash !== view.headEventHash) {
      throw new Error("checkpoint mode or evidence mismatch");
    }
    if (record.mode === "live" && (record.chainId !== 84532 ||
        record.contractAddress?.toLowerCase() !== this.#address?.toLowerCase() ||
        !/^0x[0-9a-fA-F]{64}$/.test(record.transactionHash ?? ""))) {
      throw new Error("invalid live checkpoint proof");
    }
    return record;
  }

  anchor(purchaseId: string, phase: CheckpointPhase): Promise<CheckpointRecord> {
    if (this.mode === "off") return Promise.reject(new Error("evidence checkpoints are disabled"));
    const key = `${purchaseId}:${phase}`;
    const running = this.#pending.get(key);
    if (running) return running;
    const promise = this.#anchor(purchaseId, phase).finally(() => this.#pending.delete(key));
    this.#pending.set(key, promise);
    return promise;
  }

  async #anchor(purchaseId: string, phase: CheckpointPhase): Promise<CheckpointRecord> {
    if (phase === "audit") await this.requireDecision(purchaseId);
    const view = await this.#api(purchaseId, phase);
    const existing = this.#validatedRecord(view);
    if (existing) { await this.#verifyRecorded(view, existing); return existing; }
    let record: CheckpointRecord = { mode: "mock", eventCount: view.eventCount, headEventHash: view.headEventHash };
    if (this.mode === "live") {
      if (!this.#contract || !this.#address) throw new Error("live anchor writer unavailable");
      const service = new EvidenceAnchorService({
        api: {
          head: async () => ({ purchaseId, eventCount: view.eventCount, headEventHash: view.headEventHash as `sha256:${string}` }),
          record: async ({ transactionHash }) => {
            record = { ...record, mode: "live", chainId: 84532, contractAddress: this.#address, transactionHash };
            await this.#api(purchaseId, phase, record);
          },
        },
        contract: this.#contract, chainId: 84532, contractAddress: this.#address,
      });
      await service.anchor(purchaseId);
    } else {
      // No transaction hash/chain claim in the mock record.
      await this.#api(purchaseId, phase, record);
    }
    return record;
  }
}

export function checkpointsFromEnv(env: NodeJS.ProcessEnv): AegisCheckpoints {
  const mode = env.AEGIS_CHECKPOINT_MODE || "off";
  if (mode !== "off" && mode !== "mock" && mode !== "live") throw new Error("invalid AEGIS_CHECKPOINT_MODE");
  const args = { mode, baseUrl: env.EVIDENCE_API_URL || "", internalToken: env.INTERNAL_SERVICE_TOKEN || "" } as const;
  if (mode !== "live") return new AegisCheckpoints(args);
  if (env.AEGIS_ANCHOR_WRITE_APPROVED !== "yes") throw new Error("live anchor writes require separate approval");
  const address = env.AEGIS_ANCHOR_CONTRACT_ADDRESS;
  const writer = env.AEGIS_ANCHOR_WRITER_ADDRESS;
  const key = env.AEGIS_ANCHOR_PRIVATE_KEY;
  if (!address || !isAddress(address) || !writer || !isAddress(writer) || !key || !/^0x[0-9a-fA-F]{64}$/.test(key)) {
    throw new Error("anchor contract, writer and dedicated key configuration required");
  }
  const account = privateKeyToAccount(key as Hex);
  if (account.address.toLowerCase() !== writer.toLowerCase()) throw new Error("anchor key does not match writer");
  if (!env.BASE_SEPOLIA_RPC_URL) throw new Error("anchor RPC required");
  const client = createPublicClient({ transport: http(env.BASE_SEPOLIA_RPC_URL) });
  const wallet = createWalletClient({ account, chain: baseSepolia, transport: http(env.BASE_SEPOLIA_RPC_URL) });
  const delegate = new ViemEvidenceAnchorContract({ publicClient: client, walletClient: wallet, address });
  const contract: EvidenceAnchorContract = {
    latest: async (id) => {
      if (await client.getChainId() !== 84532) throw new Error("anchor RPC is not Base Sepolia");
      return delegate.latest(id);
    },
    findConfirmed: async args => {
      if (await client.getChainId() !== 84532) throw new Error("anchor RPC is not Base Sepolia");
      return delegate.findConfirmed(args);
    },
    anchor: args => delegate.anchor(args),
  };
  return new AegisCheckpoints({ ...args, contract, contractAddress: address });
}
