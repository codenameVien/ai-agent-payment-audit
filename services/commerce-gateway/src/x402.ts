import type {
  PaymentIntent,
  PaymentPayload,
  PaymentRequired,
  PaymentRequirements,
  Permit2Authorization,
  ResourceInfo,
  SettlementResponse,
} from "./contracts.js";

export const BASE_SEPOLIA_NETWORK = "eip155:84532";
export const X402_EXACT_PERMIT2_PROXY =
  "0x402085c248EeA27D92E8b30b2C58ed07f9E20001" as const;
export const CANONICAL_PERMIT2 =
  "0x000000000022D473030F116dDEE9F6B43aC78BA3" as const;

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
  const matches = required.accepts.filter(
    (item) =>
      item.scheme === "exact" &&
      item.network === BASE_SEPOLIA_NETWORK &&
      item.amount === String(intent.amount_units) &&
      item.asset.toLowerCase() === intent.token.toLowerCase() &&
      item.payTo.toLowerCase() === intent.pay_to.toLowerCase() &&
      item.extra?.assetTransferMethod === "permit2" &&
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

export function createPermit2Authorization(args: {
  intent: PaymentIntent;
  validAfter: bigint;
  deadline: bigint;
}): Permit2Authorization {
  if (args.deadline <= args.validAfter) {
    throw new X402BindingError("Permit2 deadline must be after validAfter");
  }
  return {
    permitted: {
      token: args.intent.token,
      amount: String(args.intent.amount_units),
    },
    from: args.intent.buyer_wallet_address,
    spender: X402_EXACT_PERMIT2_PROXY,
    nonce: args.intent.permit2_nonce,
    deadline: args.deadline.toString(),
    witness: {
      to: args.intent.pay_to,
      validAfter: args.validAfter.toString(),
    },
  };
}

export function createPaymentPayload(args: {
  resource: ResourceInfo;
  requirement: PaymentRequirements;
  authorization: Permit2Authorization;
  signature: `0x${string}`;
  extensions?: Record<string, unknown>;
}): PaymentPayload {
  return {
    x402Version: 2,
    resource: args.resource,
    accepted: args.requirement,
    payload: {
      signature: args.signature,
      permit2Authorization: args.authorization,
    },
    extensions: args.extensions ?? {},
  };
}
