/**
 * The `aa-three-factor-v1` terms every runtime participant derives for itself.
 *
 * The Provider Gateway must never price a request from the body it was handed, and the
 * payment execution module must never sign an amount the challenge merely asserted. Both
 * read the same immutable decision evidence through the internal Evidence API and rebuild
 * the amount, the recipient, the token identity and the terms binding hash here.
 *
 * The money math is exact BigInt decimal arithmetic on the decimal text the buyer stored,
 * and it is cross-checked against `packages/schemas/fixtures/aa/pricing-cases.json`, the
 * same fixture the Python implementation is checked against.
 */

import { createHash } from "node:crypto";

export const AA_SCORING_POLICY_VERSION = "aa-three-factor-v1";
/** The deployed Base Sepolia token used by the current purchase flow. */
export const PBLC_TOKEN_NAME = "PBL Agent Credit";
export const PBLC_TOKEN_VERSION = "2";

export class AegisTermsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AegisTermsError";
  }
}

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

/**
 * RFC 8785 canonical JSON for the value shapes this protocol uses.
 *
 * Keys are sorted by UTF-16 code unit, which is what `Array.prototype.sort` compares and
 * what the Python `rfc8785` encoder on the other side of the boundary does. Only exact
 * integers are accepted: a fractional or non-finite number has no single canonical form
 * we are willing to guess at, and no field of a terms binding is fractional.
 */
export function canonicalJson(value: JsonValue): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new AegisTermsError("canonical JSON accepts only exact integers");
    }
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  const members = keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key]!)}`);
  return `{${members.join(",")}}`;
}

export function sha256Json(value: JsonValue): string {
  return `sha256:${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

interface ScaledDecimal {
  value: bigint;
  scale: number;
}

function scaledDecimal(text: string, field: string): ScaledDecimal {
  if (!/^[0-9]+(\.[0-9]+)?$/.test(text)) {
    throw new AegisTermsError(`${field} is not non-negative decimal text: ${text}`);
  }
  const [whole, fraction = ""] = text.split(".");
  return { value: BigInt(`${whole}${fraction}`), scale: fraction.length };
}

/** `ceil(inputTokens*inputPrice + maxOutputTokens*outputPrice)`, exact, markup 0. */
export function amountUnits(args: {
  estimatedInputTokens: number;
  maxOutputTokens: number;
  inputPricePerMillion: string;
  outputPricePerMillion: string;
}): bigint {
  for (const [field, count] of [
    ["estimatedInputTokens", args.estimatedInputTokens],
    ["maxOutputTokens", args.maxOutputTokens],
  ] as const) {
    if (!Number.isSafeInteger(count) || count <= 0) {
      throw new AegisTermsError(`${field} must be a positive safe integer`);
    }
  }
  const input = scaledDecimal(args.inputPricePerMillion, "inputPricePerMillion");
  const output = scaledDecimal(args.outputPricePerMillion, "outputPricePerMillion");
  const scale = Math.max(input.scale, output.scale);
  const lift = (item: ScaledDecimal): bigint => item.value * 10n ** BigInt(scale - item.scale);
  const total =
    BigInt(args.estimatedInputTokens) * lift(input) +
    BigInt(args.maxOutputTokens) * lift(output);
  const divisor = 10n ** BigInt(scale);
  return (total + divisor - 1n) / divisor;
}

export interface AegisTokenIdentity {
  address: string;
  chainId: number;
  decimals: number;
  name: string;
  status: string;
  symbol: string;
}

export interface AegisDecisionEvidence {
  purchaseId: string;
  decision: Record<string, unknown>;
  decisionEventHash: string;
  snapshot: Record<string, unknown>;
  snapshotEventHash: string;
  snapshotHash: string;
}

export interface AegisTerms {
  purchaseId: string;
  decisionEventHash: string;
  snapshotHash: string;
  termsBindingHash: string;
  amountUnits: bigint;
  recipient: string;
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  token: AegisTokenIdentity;
  /** Provenance of the AA numbers behind this decision: never relabel one as the other. */
  aaMode: string;
  aaMappingProvenance: string;
}

function requireRecord(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new AegisTermsError(`${field} is malformed`);
  }
  return value as Record<string, unknown>;
}

function requireString(source: Record<string, unknown>, key: string, field: string): string {
  const value = source[key];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new AegisTermsError(`${field} is malformed`);
  }
  return value;
}

function requireInteger(source: Record<string, unknown>, key: string, field: string): number {
  const value = source[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new AegisTermsError(`${field} is malformed`);
  }
  return value;
}

function requireArray(source: Record<string, unknown>, key: string, field: string): unknown[] {
  const value = source[key];
  if (!Array.isArray(value)) {
    throw new AegisTermsError(`${field} is malformed`);
  }
  return value;
}

function tokenIdentity(value: unknown): AegisTokenIdentity {
  const token = requireRecord(value, "decision token identity");
  return {
    address: requireString(token, "address", "token address").toLowerCase(),
    chainId: requireInteger(token, "chainId", "token chainId"),
    decimals: requireInteger(token, "decimals", "token decimals"),
    name: requireString(token, "name", "token name"),
    status: requireString(token, "status", "token status"),
    symbol: requireString(token, "symbol", "token symbol"),
  };
}

export function termsBindingHash(args: {
  purchaseId: string;
  snapshotHash: string;
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  amountUnits: bigint;
  recipient: string;
  token: AegisTokenIdentity;
}): string {
  if (args.amountUnits > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new AegisTermsError("amountUnits exceeds the canonical integer range");
  }
  return sha256Json({
    amountUnits: Number(args.amountUnits),
    modelVersion: args.modelVersion,
    providerId: args.providerId,
    providerModelId: args.providerModelId,
    purchaseId: args.purchaseId,
    recipient: args.recipient,
    scoringPolicyVersion: AA_SCORING_POLICY_VERSION,
    snapshotHash: args.snapshotHash,
    token: { ...args.token },
  });
}

/**
 * Rebuild the terms from the immutable decision and its AA snapshot body.
 *
 * Everything is recomputed: the amount from the snapshot's own decimal prices and the
 * stored token estimate, and the binding hash from those recomputed values. A decision
 * whose amount, recipient or binding was edited after capture cannot survive this.
 */
export function deriveTerms(evidence: AegisDecisionEvidence): AegisTerms {
  const decision = requireRecord(evidence.decision, "decision");
  const snapshot = requireRecord(evidence.snapshot, "snapshot");
  if (decision.scoringPolicyVersion !== AA_SCORING_POLICY_VERSION) {
    throw new AegisTermsError("decision is not aa-three-factor-v1");
  }
  if (decision.purchaseId !== evidence.purchaseId) {
    throw new AegisTermsError("decision belongs to another purchase");
  }
  if (
    decision.snapshotHash !== evidence.snapshotHash ||
    snapshot.purchaseId !== evidence.purchaseId
  ) {
    throw new AegisTermsError("decision cites a different AA snapshot");
  }
  const winner = requireRecord(decision.winner, "decision winner");
  const candidateKey = requireString(winner, "candidateKey", "winner candidate key");
  const candidates = requireArray(decision, "candidates", "decision candidates")
    .map((item) => requireRecord(item, "decision candidate"))
    .filter((item) => item.candidateKey === candidateKey);
  if (candidates.length !== 1) {
    throw new AegisTermsError("decision winner is not a unique priced candidate");
  }
  const priced = candidates[0]!;
  const metrics = requireRecord(priced.source, "winner AA source metrics");
  const models = requireArray(snapshot, "models", "snapshot models")
    .map((item) => requireRecord(item, "snapshot model"))
    .filter((item) => item.candidateKey === candidateKey);
  if (models.length !== 1) {
    throw new AegisTermsError("snapshot has no unique AA metrics for the winner");
  }
  const snapshotModel = models[0]!;
  for (const field of ["inputPricePerMillion", "outputPricePerMillion"] as const) {
    if (snapshotModel[field] !== metrics[field]) {
      throw new AegisTermsError(`decision ${field} disagrees with the captured snapshot`);
    }
  }
  const tokens = requireRecord(decision.tokens, "decision token estimate");
  const recomputedAmount = amountUnits({
    estimatedInputTokens: requireInteger(tokens, "estimatedInputTokens", "estimatedInputTokens"),
    maxOutputTokens: requireInteger(tokens, "maxOutputTokens", "maxOutputTokens"),
    inputPricePerMillion: requireString(
      snapshotModel,
      "inputPricePerMillion",
      "snapshot inputPricePerMillion",
    ),
    outputPricePerMillion: requireString(
      snapshotModel,
      "outputPricePerMillion",
      "snapshot outputPricePerMillion",
    ),
  });
  const storedAmount = BigInt(requireInteger(decision, "amountUnits", "decision amount"));
  if (recomputedAmount !== storedAmount) {
    throw new AegisTermsError(
      `decision amount ${storedAmount} is not the ${recomputedAmount} its AA prices produce`,
    );
  }
  if (recomputedAmount === 0n) {
    throw new AegisTermsError("zero_payment_amount_unsupported");
  }
  if (BigInt(requireInteger(winner, "amountUnits", "winner amount")) !== storedAmount) {
    throw new AegisTermsError("decision amount disagrees with its winner");
  }
  const providerId = requireString(winner, "providerId", "winner provider");
  const providerModelId = requireString(winner, "providerModelId", "winner model");
  const modelVersion = requireString(winner, "modelVersion", "winner model version");
  if (
    priced.providerId !== providerId ||
    priced.providerModelId !== providerModelId ||
    priced.modelVersion !== modelVersion
  ) {
    throw new AegisTermsError("decision winner identity disagrees with its candidate");
  }
  const recipient = requireString(priced, "recipient", "winner recipient").toLowerCase();
  const token = tokenIdentity(decision.token);
  const binding = termsBindingHash({
    purchaseId: evidence.purchaseId,
    snapshotHash: evidence.snapshotHash,
    providerId,
    providerModelId,
    modelVersion,
    amountUnits: recomputedAmount,
    recipient,
    token,
  });
  if (binding !== decision.termsBindingHash) {
    throw new AegisTermsError("decision terms binding does not match its own terms");
  }
  return {
    purchaseId: evidence.purchaseId,
    decisionEventHash: evidence.decisionEventHash,
    snapshotHash: evidence.snapshotHash,
    termsBindingHash: binding,
    amountUnits: recomputedAmount,
    recipient,
    providerId,
    providerModelId,
    modelVersion,
    token,
    aaMode: requireString(snapshot, "mode", "snapshot mode"),
    aaMappingProvenance: requireString(
      priced,
      "mappingProvenance",
      "winner mapping provenance",
    ),
  };
}
