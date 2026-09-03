import type {
  PaymentIntent,
  PaymentPayload,
  PaymentRequired,
  PaymentRequirements,
  Erc3009Authorization,
  Permit2Signer,
  Permit2Authorization,
  ResourceInfo,
  SettlementResponse,
} from "./contracts.js";

export const BASE_SEPOLIA_NETWORK = "eip155:84532";
export const X402_EXACT_PERMIT2_PROXY =
  "0x402085c248EeA27D92E8b30b2C58ed07f9E20001" as const;
export const CANONICAL_PERMIT2 =
  "0x000000000022D473030F116dDEE9F6B43aC78BA3" as const;
export const EIP2612_GAS_SPONSORING = "eip2612GasSponsoring";
export const PBLC_TOKEN_NAME = "PBL Agent Credit";
export const PBLC_TOKEN_VERSION = "1";
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
  const matches = required.accepts.filter(
    (item) =>
      item.scheme === "exact" &&
      item.network === BASE_SEPOLIA_NETWORK &&
      item.amount === String(intent.amount_units) &&
      item.asset.toLowerCase() === intent.token.toLowerCase() &&
      item.payTo.toLowerCase() === intent.pay_to.toLowerCase() &&
      item.extra?.assetTransferMethod === (intent.transfer_method ?? "permit2") &&
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

export async function createEip2612GasSponsoringPayloadExtension(args: {
  declaredExtensions?: Record<string, unknown>;
  requirement: PaymentRequirements;
  authorization: Permit2Authorization;
  signer: Permit2Signer;
}): Promise<Record<string, unknown>> {
  const declaration = args.declaredExtensions?.[EIP2612_GAS_SPONSORING];
  if (declaration === undefined) return {};
  if (typeof declaration !== "object" || declaration === null) {
    throw new X402BindingError("EIP-2612 gas sponsorship declaration is malformed");
  }
  const declarationInfo = (declaration as Record<string, unknown>).info;
  const declarationSchema = (declaration as Record<string, unknown>).schema;
  if (
    typeof declarationInfo !== "object" ||
    declarationInfo === null ||
    (declarationInfo as Record<string, unknown>).version !== "1" ||
    typeof declarationSchema !== "object" ||
    declarationSchema === null
  ) {
    throw new X402BindingError("EIP-2612 gas sponsorship version is unsupported");
  }
  if (
    args.requirement.extra?.name !== PBLC_TOKEN_NAME ||
    args.requirement.extra?.version !== PBLC_TOKEN_VERSION
  ) {
    throw new X402BindingError("EIP-2612 token domain does not match PBLC");
  }
  if (args.signer.signEip2612Permit === undefined) {
    throw new X402BindingError("EIP-2612 permit signer is unavailable");
  }
  const info = await args.signer.signEip2612Permit({
    authorization: args.authorization,
    tokenName: PBLC_TOKEN_NAME,
    tokenVersion: PBLC_TOKEN_VERSION,
  });
  if (
    info.from.toLowerCase() !== args.authorization.from.toLowerCase() ||
    info.asset.toLowerCase() !== args.authorization.permitted.token.toLowerCase() ||
    info.spender.toLowerCase() !== CANONICAL_PERMIT2.toLowerCase() ||
    info.amount !== args.authorization.permitted.amount ||
    info.deadline !== args.authorization.deadline
  ) {
    throw new X402BindingError("EIP-2612 permit is not bound to the payment");
  }
  return { [EIP2612_GAS_SPONSORING]: { info } };
}
