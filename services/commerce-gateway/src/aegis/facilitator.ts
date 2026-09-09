/**
 * Local Mock Facilitator for the x402 `exact` + ERC-3009 scheme.
 *
 * The cryptography is real: the EIP-712 `TransferWithAuthorization` signature is
 * recovered and must belong to the declared payer, and the authorization nonce is
 * single-use, exactly as the token contract would enforce it. What is mocked is the
 * chain: nothing is submitted, no receipt exists and the returned settlement identifier
 * is deliberately not shaped like an EVM transaction hash so it can never be rendered as
 * one or linked to an explorer.
 */

import { createHash } from "node:crypto";

import { recoverTypedDataAddress, type Address, type Hex } from "viem";

import { TRANSFER_WITH_AUTHORIZATION_TYPES } from "../eip712.js";
import { MOCK_EXECUTION_MODE } from "./providers.js";
import { sha256Json } from "./terms.js";
import {
  HEX_32_BYTES,
  HEX_ADDRESS,
  type AegisPaymentPayload,
  type AegisPaymentRequirements,
  type AegisSettlementResponse,
} from "./x402.js";

export const MOCK_SETTLEMENT_PREFIX = "x402mock:";

export interface FacilitatorVerification {
  isValid: boolean;
  payer?: string;
  invalidReason?: string;
}

interface SettlementContext {
  paymentPayload: AegisPaymentPayload;
  paymentRequirements: AegisPaymentRequirements;
}

/**
 * One consumed authorization, exactly as the token contract records it.
 *
 * The contract keys `_authorizationStates` by `(from, nonce)`, so this mock does too. The
 * fingerprint is the immutable authorization and the requirements it was settled against:
 * replaying that exact context is idempotent, while anything else carrying the same nonce
 * is a reuse attempt and is refused.
 */
interface ConsumedAuthorization {
  fingerprint: string;
  settlement: Promise<AegisSettlementResponse>;
}

function chainIdOf(network: string): number {
  const match = /^eip155:([0-9]+)$/.exec(network);
  if (match === null) throw new Error(`unsupported network: ${network}`);
  return Number(match[1]);
}

function authorizationKey(from: string, nonce: string): string {
  return `${from.toLowerCase()}:${nonce.toLowerCase()}`;
}

function contextFingerprint(context: SettlementContext): string {
  const authorization = context.paymentPayload.payload.authorization;
  const requirements = context.paymentRequirements;
  return sha256Json({
    amount: requirements.amount,
    asset: requirements.asset.toLowerCase(),
    from: authorization.from.toLowerCase(),
    network: requirements.network,
    nonce: authorization.nonce.toLowerCase(),
    payTo: requirements.payTo.toLowerCase(),
    scheme: requirements.scheme,
    signature: context.paymentPayload.payload.signature.toLowerCase(),
    to: authorization.to.toLowerCase(),
    validAfter: authorization.validAfter,
    validBefore: authorization.validBefore,
    value: authorization.value,
  });
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export class MockFacilitator {
  /** One `(from, nonce)` pair, one settlement. This is what makes a replay impossible. */
  readonly #used = new Map<string, ConsumedAuthorization>();
  readonly #nowSeconds: () => bigint;
  readonly #beforeSettle?: (context: SettlementContext) => Promise<void>;

  constructor(options: {
    nowSeconds?: () => bigint;
    beforeSettle?: (context: SettlementContext) => Promise<void>;
  } = {}) {
    this.#nowSeconds = options.nowSeconds ?? (() => BigInt(Math.floor(Date.now() / 1000)));
    this.#beforeSettle = options.beforeSettle;
  }

  verify(context: SettlementContext): Promise<FacilitatorVerification> {
    return this.#verifyAuthorization(context, false);
  }

  async #verifyAuthorization(
    context: SettlementContext,
    ownsClaim: boolean,
  ): Promise<FacilitatorVerification> {
    const { paymentPayload, paymentRequirements } = context;
    const authorization = paymentPayload.payload.authorization;
    if (paymentRequirements.scheme !== "exact") {
      return { isValid: false, invalidReason: "unsupported scheme" };
    }
    if (paymentRequirements.extra?.assetTransferMethod !== "eip3009") {
      return { isValid: false, invalidReason: "unsupported asset transfer method" };
    }
    for (const [field, value] of [
      ["from", authorization.from],
      ["to", authorization.to],
      ["asset", paymentRequirements.asset],
      ["payTo", paymentRequirements.payTo],
    ] as const) {
      if (!HEX_ADDRESS.test(value)) {
        return { isValid: false, invalidReason: `${field} is not an address` };
      }
    }
    if (!HEX_32_BYTES.test(authorization.nonce)) {
      return { isValid: false, invalidReason: "authorization nonce is malformed" };
    }
    if (authorization.value !== paymentRequirements.amount) {
      return { isValid: false, invalidReason: "authorized value is not the required amount" };
    }
    if (authorization.to.toLowerCase() !== paymentRequirements.payTo.toLowerCase()) {
      return { isValid: false, invalidReason: "authorized recipient is not payTo" };
    }
    const now = this.#nowSeconds();
    // AEGISToken.transferWithAuthorization reverts on `block.timestamp <= validAfter`, so
    // an authorization whose validAfter equals now would fail on chain. The mock refuses
    // it too rather than settling something the real contract would reject.
    if (BigInt(authorization.validAfter) >= now) {
      return { isValid: false, invalidReason: "authorization is not valid yet" };
    }
    if (BigInt(authorization.validBefore) <= now) {
      return { isValid: false, invalidReason: "authorization is expired" };
    }
    let recovered: Address;
    try {
      recovered = await recoverTypedDataAddress({
        domain: {
          name: paymentRequirements.extra.name,
          version: paymentRequirements.extra.version,
          chainId: chainIdOf(paymentRequirements.network),
          verifyingContract: paymentRequirements.asset as Address,
        },
        types: TRANSFER_WITH_AUTHORIZATION_TYPES,
        primaryType: "TransferWithAuthorization",
        message: {
          from: authorization.from as Address,
          to: authorization.to as Address,
          value: BigInt(authorization.value),
          validAfter: BigInt(authorization.validAfter),
          validBefore: BigInt(authorization.validBefore),
          nonce: authorization.nonce as Hex,
        },
        signature: paymentPayload.payload.signature as Hex,
      });
    } catch {
      return { isValid: false, invalidReason: "signature is not recoverable" };
    }
    if (recovered.toLowerCase() !== authorization.from.toLowerCase()) {
      return { isValid: false, invalidReason: "signature does not belong to the payer" };
    }
    if (!ownsClaim && this.#used.has(authorizationKey(authorization.from, authorization.nonce))) {
      return { isValid: false, invalidReason: "authorization is already used" };
    }
    return { isValid: true, payer: recovered.toLowerCase() };
  }

  /**
   * Consume the authorization once.
   *
   * The `(from, nonce)` slot is claimed synchronously, before the first `await`, so two
   * concurrent settlements of two different authorizations that share a nonce cannot both
   * pass verification and both succeed. Only a byte-identical replay of the same context
   * shares the in-flight settlement; a different context on a consumed nonce is refused.
   * A failed attempt releases the slot, because a rejected authorization was not spent.
   */
  settle(context: SettlementContext): Promise<AegisSettlementResponse> {
    const authorization = context.paymentPayload.payload.authorization;
    if (
      typeof authorization?.from !== "string" ||
      typeof authorization?.nonce !== "string" ||
      typeof context.paymentPayload.payload.signature !== "string"
    ) {
      return Promise.resolve({
        success: false,
        transaction: "",
        network: String(context.paymentRequirements?.network ?? ""),
        errorReason: "authorization is malformed",
      });
    }
    const key = authorizationKey(authorization.from, authorization.nonce);
    const fingerprint = contextFingerprint(context);
    const claimed = this.#used.get(key);
    if (claimed !== undefined) {
      if (claimed.fingerprint !== fingerprint) {
        return Promise.resolve({
          success: false,
          transaction: "",
          network: context.paymentRequirements.network,
          errorReason: "authorization is already used",
        });
      }
      return claimed.settlement;
    }
    const settlement = this.#execute(context, key, fingerprint);
    this.#used.set(key, { fingerprint, settlement });
    return settlement;
  }

  async #execute(
    context: SettlementContext,
    key: string,
    fingerprint: string,
  ): Promise<AegisSettlementResponse> {
    const verification = await this.#verifyAuthorization(context, true);
    if (!verification.isValid || verification.payer === undefined) {
      this.#used.delete(key);
      return {
        success: false,
        transaction: "",
        network: context.paymentRequirements.network,
        errorReason: verification.invalidReason ?? "payment is not valid",
      };
    }
    if (this.#beforeSettle !== undefined) await this.#beforeSettle(context);
    return {
      success: true,
      // Not an EVM hash: this run never submitted a transaction.
      transaction: `${MOCK_SETTLEMENT_PREFIX}${createHash("sha256")
        .update(fingerprint)
        .digest("hex")
        .slice(0, 32)}`,
      network: context.paymentRequirements.network,
      payer: verification.payer,
      amount: context.paymentRequirements.amount,
    };
  }

  async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", role: "facilitator", executionMode: MOCK_EXECUTION_MODE });
    }
    if (request.method !== "POST") return json({ error: "not found" }, 404);
    let context: SettlementContext;
    try {
      context = (await request.json()) as SettlementContext;
      if (
        typeof context?.paymentPayload?.payload?.authorization !== "object" ||
        typeof context?.paymentRequirements !== "object"
      ) {
        throw new Error("malformed settlement context");
      }
    } catch {
      return json({ error: "malformed settlement context" }, 400);
    }
    if (url.pathname === "/verify") return json(await this.verify(context));
    if (url.pathname === "/settle") return json(await this.settle(context));
    return json({ error: "not found" }, 404);
  }
}
