import type {
  PaymentIntent,
  PaymentPayload,
  PaymentRequired,
  PaymentRequirements,
  Erc3009Authorization,
  ResourceInfo,
  SettlementResponse,
} from "./contracts.js";

export const BASE_SEPOLIA_NETWORK = "eip155:84532";
export const PBLC_TOKEN_NAME = "PBL Agent Credit";
export const PBLC_V2_TOKEN_VERSION = "2";

export class X402BindingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "X402BindingError";
  }
}

export function encodeHeader(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function decodeObject(header: string, label: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(Buffer.from(header, "base64").toString("utf8"));
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error("not an object");
    }
    return value as Record<string, unknown>;
  } catch (error) {
    throw new X402BindingError(`${label} header is malformed`);
  }
}

export function decodePaymentRequired(header: string): PaymentRequired {
  const value = decodeObject(header, "PAYMENT-REQUIRED");
  if (value.x402Version !== 2 || !Array.isArray(value.accepts)) {
    throw new X402BindingError("PAYMENT-REQUIRED is not x402 v2");
  }
  if (typeof value.resource !== "object" || value.resource === null) {
    throw new X402BindingError("PAYMENT-REQUIRED resource is missing");
  }
  return value as unknown as PaymentRequired;
}

export function decodeSettlementResponse(header: string): SettlementResponse {
  const value = decodeObject(header, "PAYMENT-RESPONSE");
  if (
    typeof value.success !== "boolean" ||
    typeof value.transaction !== "string" ||
    typeof value.network !== "string"
  ) {
    throw new X402BindingError("PAYMENT-RESPONSE is malformed");
  }
  return value as unknown as SettlementResponse;
}

export function selectBoundRequirement(
  required: PaymentRequired,
  intent: PaymentIntent,
): PaymentRequirements {
  if (intent.transfer_method !== "eip3009") {
    throw new X402BindingError("legacy Permit2 payment intents are read-only");
  }
  const matches = required.accepts.filter(
    (item) =>
      item.scheme === "exact" &&
      item.network === BASE_SEPOLIA_NETWORK &&
      item.amount === String(intent.amount_units) &&
      item.asset.toLowerCase() === intent.token.toLowerCase() &&
      item.payTo.toLowerCase() === intent.pay_to.toLowerCase() &&
      item.extra?.assetTransferMethod === "eip3009" &&
      Number.isSafeInteger(item.maxTimeoutSeconds) &&
      item.maxTimeoutSeconds > 0,
  );
  if (matches.length !== 1) {
    throw new X402BindingError(
      "402 requirements do not uniquely match decision token/amount/recipient",
    );
  }
  return matches[0]!;
}

export function createErc3009Authorization(args: {
  intent: PaymentIntent;
  validAfter: bigint;
  validBefore: bigint;
}): Erc3009Authorization {
  if (args.intent.transfer_method !== "eip3009") {
    throw new X402BindingError("payment intent is not ERC-3009");
  }
  if (!args.intent.authorization_nonce || !/^0x[0-9a-fA-F]{64}$/.test(args.intent.authorization_nonce)) {
    throw new X402BindingError("ERC-3009 authorization nonce is missing or malformed");
  }
  if (args.validBefore <= args.validAfter) {
    throw new X402BindingError("ERC-3009 validBefore must be after validAfter");
  }
  return {
    from: args.intent.buyer_wallet_address,
    to: args.intent.pay_to,
    value: String(args.intent.amount_units),
    validAfter: args.validAfter.toString(),
    validBefore: args.validBefore.toString(),
    nonce: args.intent.authorization_nonce,
  };
}

export function createErc3009PaymentPayload(args: {
  resource: ResourceInfo;
  requirement: PaymentRequirements;
  authorization: Erc3009Authorization;
  signature: `0x${string}`;
}): PaymentPayload {
  return {
    x402Version: 2,
    resource: args.resource,
    accepted: args.requirement,
    payload: { signature: args.signature, authorization: args.authorization },
    extensions: {},
  };
}
