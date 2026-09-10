/**
 * x402 v2 `exact` + ERC-3009 for PBLC V2, with the decision binding carried explicitly.
 *
 * A stock 402 only says "pay this much to this address". That is not enough here: the
 * audit thesis is that a payment is provably the one the recorded decision fixed, so the
 * challenge also carries the purchase, the exact provider model version, the AA snapshot
 * hash, the decision event hash and the terms binding hash. The payment execution module
 * compares every one of them against evidence it fetched itself before it signs.
 */

import { encodeHeader, X402BindingError } from "../x402.js";
import { PBLC_TOKEN_NAME, PBLC_TOKEN_VERSION, type AegisTerms } from "./terms.js";

export { encodeHeader, X402BindingError };

export const HEX_ADDRESS = /^0x[0-9a-fA-F]{40}$/;
export const HEX_32_BYTES = /^0x[0-9a-fA-F]{64}$/;

export interface AegisPaymentRequirements {
  scheme: "exact";
  network: string;
  amount: string;
  asset: string;
  payTo: string;
  maxTimeoutSeconds: number;
  extra: {
    assetTransferMethod: "eip3009";
    name: string;
    version: string;
  };
}

/** The decision facts a 402 and its signature are both bound to. */
export interface AegisBinding {
  purchaseId: string;
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  snapshotHash: string;
  decisionEventHash: string;
  termsBindingHash: string;
  scoringPolicyVersion: string;
  /** Mock execution and synthetic AA data are labelled in the protocol itself. */
  executionMode: string;
  aaMode: string;
  aaMappingProvenance: string;
}

export interface AegisResourceInfo {
  url: string;
  description: string;
  mimeType: string;
}

export interface AegisPaymentRequired {
  x402Version: 2;
  error: string;
  resource: AegisResourceInfo;
  accepts: AegisPaymentRequirements[];
  extensions: { aegis: AegisBinding };
}

export interface Erc3009AuthorizationPayload {
  from: string;
  to: string;
  value: string;
  validAfter: string;
  validBefore: string;
  nonce: string;
}

export interface AegisPaymentPayload {
  x402Version: 2;
  resource: AegisResourceInfo;
  accepted: AegisPaymentRequirements;
  payload: { signature: string; authorization: Erc3009AuthorizationPayload };
  extensions: { aegis: AegisBinding };
}

export interface AegisSettlementResponse {
  success: boolean;
  transaction: string;
  network: string;
  payer?: string;
  amount?: string;
  errorReason?: string;
}

export function bindingFor(terms: AegisTerms, executionMode: string): AegisBinding {
  return {
    purchaseId: terms.purchaseId,
    providerId: terms.providerId,
    providerModelId: terms.providerModelId,
    modelVersion: terms.modelVersion,
    snapshotHash: terms.snapshotHash,
    decisionEventHash: terms.decisionEventHash,
    termsBindingHash: terms.termsBindingHash,
    scoringPolicyVersion: "aa-three-factor-v1",
    executionMode,
    aaMode: terms.aaMode,
    aaMappingProvenance: terms.aaMappingProvenance,
  };
}

export function requirementsFor(args: {
  terms: AegisTerms;
  network: string;
  maxTimeoutSeconds: number;
  tokenName?: string;
  tokenVersion?: string;
}): AegisPaymentRequirements {
  return {
    scheme: "exact",
    network: args.network,
    amount: args.terms.amountUnits.toString(),
    asset: args.terms.token.address,
    payTo: args.terms.recipient,
    maxTimeoutSeconds: args.maxTimeoutSeconds,
    extra: {
      assetTransferMethod: "eip3009",
      name: args.tokenName ?? PBLC_TOKEN_NAME,
      version: args.tokenVersion ?? PBLC_TOKEN_VERSION,
    },
  };
}

function decodeObject(header: string, label: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(Buffer.from(header, "base64").toString("utf8"));
  } catch {
    throw new X402BindingError(`${label} header is malformed`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new X402BindingError(`${label} header is malformed`);
  }
  return value as Record<string, unknown>;
}

export function decodeAegisPaymentRequired(header: string): AegisPaymentRequired {
  const value = decodeObject(header, "PAYMENT-REQUIRED");
  if (value.x402Version !== 2 || !Array.isArray(value.accepts)) {
    throw new X402BindingError("PAYMENT-REQUIRED is not x402 v2");
  }
  if (typeof value.resource !== "object" || value.resource === null) {
    throw new X402BindingError("PAYMENT-REQUIRED resource is missing");
  }
  const extensions = value.extensions;
  if (
    typeof extensions !== "object" ||
    extensions === null ||
    typeof (extensions as Record<string, unknown>).aegis !== "object"
  ) {
    throw new X402BindingError("PAYMENT-REQUIRED carries no AEGIS decision binding");
  }
  return value as unknown as AegisPaymentRequired;
}

export function decodeAegisPaymentPayload(header: string): AegisPaymentPayload {
  const value = decodeObject(header, "PAYMENT-SIGNATURE");
  const payload = value.payload;
  const accepted = value.accepted;
  if (
    value.x402Version !== 2 ||
    typeof accepted !== "object" ||
    accepted === null ||
    typeof payload !== "object" ||
    payload === null
  ) {
    throw new X402BindingError("PAYMENT-SIGNATURE payload is malformed");
  }
  const inner = payload as Record<string, unknown>;
  const authorization = inner.authorization;
  if (
    typeof inner.signature !== "string" ||
    !HEX_32_BYTES.test(inner.signature.slice(0, 66)) ||
    typeof authorization !== "object" ||
    authorization === null
  ) {
    throw new X402BindingError("PAYMENT-SIGNATURE authorization is malformed");
  }
  return value as unknown as AegisPaymentPayload;
}

export function decodeAegisSettlement(header: string): AegisSettlementResponse {
  const value = decodeObject(header, "PAYMENT-RESPONSE");
  if (
    typeof value.success !== "boolean" ||
    typeof value.transaction !== "string" ||
    typeof value.network !== "string"
  ) {
    throw new X402BindingError("PAYMENT-RESPONSE is malformed");
  }
  return value as unknown as AegisSettlementResponse;
}

/**
 * Every binding field must equal the evidence-derived value; nothing is optional.
 *
 * `expectedExecutionMode` is the caller's own trusted configuration. Deriving it from the
 * received binding would make that field compare against itself, which is exactly how a
 * mock settlement could arrive labelled as a live one.
 */
export function assertBinding(
  binding: AegisBinding,
  terms: AegisTerms,
  expectedExecutionMode: string,
): void {
  const expected = bindingFor(terms, expectedExecutionMode);
  for (const key of Object.keys(expected) as (keyof AegisBinding)[]) {
    if (binding[key] !== expected[key]) {
      throw new X402BindingError(`402 decision binding field ${key} is not the decided value`);
    }
  }
}

/**
 * Pick the single requirement that matches the evidence-derived terms exactly.
 *
 * An amount, asset, recipient, network or token domain that differs by any amount is a
 * refusal, not a negotiation: this runtime has no counteroffer path.
 */
export function selectBoundRequirement(args: {
  required: AegisPaymentRequired;
  terms: AegisTerms;
  network: string;
  executionMode: string;
  tokenName?: string;
  tokenVersion?: string;
}): AegisPaymentRequirements {
  assertBinding(args.required.extensions.aegis, args.terms, args.executionMode);
  const expected = requirementsFor({
    terms: args.terms,
    network: args.network,
    maxTimeoutSeconds: 0,
    tokenName: args.tokenName,
    tokenVersion: args.tokenVersion,
  });
  const matches = args.required.accepts.filter(
    (item) =>
      item.scheme === expected.scheme &&
      item.network === expected.network &&
      item.amount === expected.amount &&
      typeof item.asset === "string" &&
      item.asset.toLowerCase() === expected.asset &&
      typeof item.payTo === "string" &&
      item.payTo.toLowerCase() === expected.payTo &&
      Number.isSafeInteger(item.maxTimeoutSeconds) &&
      item.maxTimeoutSeconds > 0 &&
      item.extra?.assetTransferMethod === "eip3009" &&
      item.extra?.name === expected.extra.name &&
      item.extra?.version === expected.extra.version,
  );
  if (matches.length !== 1) {
    throw new X402BindingError(
      "402 requirements do not uniquely match the decided amount, token and recipient",
    );
  }
  return matches[0]!;
}
