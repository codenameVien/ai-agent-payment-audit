/**
 * Read-only helpers for `aa-three-factor-v1` requests and their decision evidence.
 *
 * Every value here comes from an append-only event the Audit Evidence API already
 * returned. Prices, completion times and scores stay exactly as they were stored -
 * stored decimal text is never re-parsed into a binary float - and a field the evidence
 * does not carry stays `null`, so a surface can render it as unavailable instead of
 * inventing a number. Nothing here reads payment internals: the payment lifecycle is
 * taken from the public purchase summary only.
 *
 * The stored-request discriminator lives here too, so the resume policy below and the
 * evidence readers can never disagree about what an `aegis-aa-v1` purchase is.
 */

import type { EvidenceEvent, PurchaseDetail } from "./types";

export const AEGIS_REQUEST_SCHEMA_VERSION = "aegis-aa-v1";
export const AEGIS_SCORING_POLICY_VERSION = "aa-three-factor-v1";

type Json = Record<string, unknown>;

function record(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : {};
}

function records(value: unknown): Json[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Json =>
          typeof item === "object" && item !== null && !Array.isArray(item),
      )
    : [];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

/** Stored decimal evidence is kept as text; only an integer stays a number. */
function decimalText(value: unknown): string | null {
  if (typeof value === "string" && value.trim() !== "") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

export function isAegisRequest(requestSummary: unknown): boolean {
  return record(requestSummary).request_schema_version === AEGIS_REQUEST_SCHEMA_VERSION;
}

/**
 * Storage key of a pending `aegis-aa-v1` request. It is deliberately not one of the
 * keys the superseded PBLC runtime wrote: an id left behind by that runtime must never
 * become the target of a new run.
 */
export const AEGIS_PENDING_PURCHASE_KEY = "aegis:purchase-request-id";

/** Keys written by the superseded runtime. Read-only history: never written, never removed. */
export const HISTORICAL_PENDING_PURCHASE_KEYS = [
  "pbl:purchase-request-id",
  "pbl:normal-experiment-purchase-id",
];

export interface PendingStore {
  getItem(key: string): string | null;
}

export interface HistoricalPending {
  key: string;
  purchaseId: string;
}

export interface StoredRequestFacts {
  purchaseId: string;
  isAegis: boolean;
  promptHash: string | null;
  originalPriority: string | null;
  effectivePriority: string | null;
  budgetUnits: number | null;
  settled: boolean;
  audited: boolean;
}

export interface PendingResolution {
  /**
   * `resume` is the only state a stored id may be run under, and it is reached only
   * after a public detail GET proved the purchase is an unfinished `aegis-aa-v1` one.
   * `blocked` means the lookup failed, so neither resuming nor starting a new run is
   * safe until it is checked again.
   */
  status: "none" | "resume" | "blocked" | "foreign" | "completed";
  purchaseId: string | null;
  pending: StoredRequestFacts | null;
  historical: HistoricalPending[];
}

export function readStoredRequestFacts(detail: PurchaseDetail): StoredRequestFacts {
  const summary = record(detail.summary.request_summary);
  const requested = detail.events.find((event) => event.type === "REQUESTED");
  return {
    purchaseId: detail.summary.purchase_id,
    isAegis: isAegisRequest(summary),
    promptHash: text(summary.prompt_hash),
    originalPriority: text(summary.original_priority),
    effectivePriority: text(summary.effective_priority),
    budgetUnits:
      requested === undefined ? null : integer((requested.payload as Json).budgetUnits),
    settled: detail.summary.payment_status === "PAYMENT_SETTLED",
    audited: detail.audit !== null,
  };
}

export async function resolvePendingRequest(
  store: PendingStore,
  load: (purchaseId: string) => Promise<PurchaseDetail>,
): Promise<PendingResolution> {
  const historical: HistoricalPending[] = [];
  for (const key of HISTORICAL_PENDING_PURCHASE_KEYS) {
    const stored = store.getItem(key);
    if (stored !== null && stored.trim() !== "") {
      historical.push({ key, purchaseId: stored });
    }
  }
  const pendingId = store.getItem(AEGIS_PENDING_PURCHASE_KEY);
  if (pendingId === null || pendingId.trim() === "") {
    return { status: "none", purchaseId: null, pending: null, historical };
  }
  let detail: PurchaseDetail;
  try {
    detail = await load(pendingId);
  } catch {
    return { status: "blocked", purchaseId: pendingId, pending: null, historical };
  }
  const facts = readStoredRequestFacts(detail);
  if (!facts.isAegis) {
    return { status: "foreign", purchaseId: pendingId, pending: facts, historical };
  }
  if (facts.settled || facts.audited) {
    return { status: "completed", purchaseId: pendingId, pending: facts, historical };
  }
  return { status: "resume", purchaseId: pendingId, pending: facts, historical };
}

export interface RequestInput {
  promptHash: string;
  /** `null` means the form sent no priority at all, so the server classified it. */
  priority: string | null;
  /** `null` means the budget field was left empty and the wallet limit applied. */
  budgetUnits: number | null;
}

/**
 * The single gate the request surface uses for both its button and its submit handler.
 *
 * `pending === null` is the unresolved lookup, not "nothing stored": until the detail
 * GET answers, a stored id could still be a resumable purchase, so creating a second
 * purchase for the same request would run it twice under two ids. Server-side
 * per-purchaseId idempotency cannot catch that, so the check has to finish first.
 */
export function canSubmitRequest(
  pending: PendingResolution | null,
  state: { busy: boolean; acknowledged: boolean },
): boolean {
  if (state.busy || !state.acknowledged) return false;
  if (pending === null) return false;
  return pending.status !== "blocked";
}

/**
 * A pending purchase may only be re-run for the request it was created from. An empty
 * budget field cannot be distinguished from the wallet limit the server stored, so it
 * is not treated as a change; the stored units are disclosed next to the resume notice
 * instead of being reused silently.
 */
export function matchesPendingRequest(
  facts: StoredRequestFacts,
  input: RequestInput,
): boolean {
  if (facts.promptHash !== input.promptHash) return false;
  if (facts.originalPriority !== input.priority) return false;
  if (input.budgetUnits !== null && facts.budgetUnits !== input.budgetUnits) return false;
  return true;
}

/** The same `sha256:<hex>` form the server stores for a normalized request. */
export async function promptHash(prompt: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(prompt),
  );
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return `sha256:${hex}`;
}

export const AEGIS_PRIORITY_LABELS: Record<string, string> = {
  default: "기본",
  price: "가격 우선",
  speed: "속도 우선",
  intelligence: "성능 우선",
};

export const AEGIS_PRIORITY_REASON_LABELS: Record<string, string> = {
  explicit_priority: "명시적 우선순위",
  keyword_match: "요청 문구 키워드 분류",
  no_keyword_match: "분류 키워드 없음 · 기본 적용",
  conflicting_keyword_match: "상충 키워드 · 기본 적용",
};

export const AEGIS_REJECTION_LABELS: Record<string, string> = {
  over_budget: "예산 초과",
  missing_capability: "필수 기능 미지원",
  completion_time_limit: "완료시간 한도 초과",
  provider_not_allowed: "허용되지 않은 제공자",
};

/**
 * Form-only presentation of the fixed weight table. A recorded purchase always renders
 * the weights stored in its own DECIDED event, never this table.
 */
export const AEGIS_PRIORITY_OPTIONS: {
  value: string;
  label: string;
  note: string;
}[] = [
  {
    value: "auto",
    label: "요청에서 자동 판단",
    note: "priority를 보내지 않고 서버의 결정적 키워드 분류에 맡깁니다.",
  },
  { value: "default", label: "기본", note: "가격 40 / 완료시간 30 / 성능 30" },
  { value: "price", label: "가격 우선", note: "가격 60 / 완료시간 20 / 성능 20" },
  { value: "speed", label: "속도 우선", note: "가격 20 / 완료시간 60 / 성능 20" },
  {
    value: "intelligence",
    label: "성능 우선",
    note: "가격 20 / 완료시간 20 / 성능 60",
  },
];

export function describeRejection(reasons: string[]): string {
  if (reasons.length === 0) return "사유가 기록되지 않았습니다";
  return reasons.map((reason) => AEGIS_REJECTION_LABELS[reason] ?? reason).join(" · ");
}

/**
 * Shorten stored decimal text for a table cell by truncating - never rounding - the
 * fraction. The exact stored text stays available in the cell title and in the raw
 * event payload, so a shortened score is never the only value on screen.
 */
export function shortDecimal(value: string | null, fractionDigits = 2): string {
  if (value === null) return "—";
  const [whole, fraction = ""] = value.split(".");
  const kept = fraction.slice(0, fractionDigits).replace(/0+$/, "");
  return kept === "" ? whole : `${whole}.${kept}`;
}

export interface AegisScores {
  price: string | null;
  completionTime: string | null;
  intelligence: string | null;
  total: string | null;
}

export interface AegisCandidateRow {
  key: string;
  providerId: string | null;
  providerModelId: string | null;
  modelVersion: string | null;
  aaModelId: string | null;
  aaName: string | null;
  mappingProvenance: string | null;
  amountUnits: number | null;
  estimatedCompletionMs: string | null;
  completionBasis: string | null;
  intelligenceIndex: string | null;
  inputPricePerMillion: string | null;
  outputPricePerMillion: string | null;
  medianEndToEndSeconds: string | null;
  rank: number | null;
  scores: AegisScores | null;
  outcome: "winner" | "eligible" | "rejected" | "unscored";
  rejectionReasons: string[];
}

export interface AegisPriorityView {
  effective: string | null;
  original: string | null;
  reason: string | null;
  method: string | null;
  matchedKeywords: string[];
}

export interface AegisWeights {
  price: number | null;
  completionTime: number | null;
  intelligence: number | null;
}

export interface AegisTokenIdentity {
  name: string | null;
  symbol: string | null;
  decimals: number | null;
  address: string | null;
  chainId: number | null;
  status: string | null;
}

export interface AegisTokenEstimate {
  estimatedInputTokens: number | null;
  maxOutputTokens: number | null;
  estimationMethod: string | null;
  isEstimate: boolean;
}

export interface AegisDecisionView {
  scoringPolicyVersion: string | null;
  priority: AegisPriorityView;
  weights: AegisWeights;
  amountUnits: number | null;
  token: AegisTokenIdentity | null;
  tokens: AegisTokenEstimate | null;
  catalogVersion: string | null;
  snapshotId: string | null;
  snapshotHash: string | null;
  termsBindingHash: string | null;
  explanation: string | null;
  generatedExplanation: string | null;
  winnerKey: string | null;
  rows: AegisCandidateRow[];
}

export interface AegisSnapshotView {
  snapshotId: string | null;
  snapshotHash: string | null;
  mode: string | null;
  sourceUrl: string | null;
  attributionUrl: string | null;
  catalogVersion: string | null;
  catalogProvenance: string[];
  completionBenchmark: string | null;
  totalModelCount: number | null;
  pageCount: number | null;
  fetchedAt: string | null;
}

export interface AegisDeliveryView {
  executionMode: string | null;
  observedExecutionMs: number | null;
  providerId: string | null;
  modelId: string | null;
  modelVersion: string | null;
  responseId: string | null;
}

function scores(value: unknown): AegisScores | null {
  const raw = record(value);
  const total = decimalText(raw.total);
  if (total === null) return null;
  return {
    price: decimalText(raw.price),
    completionTime: decimalText(raw.completionTime),
    intelligence: decimalText(raw.intelligence),
    total,
  };
}

function candidateRows(payload: Json): AegisCandidateRow[] {
  const winner = record(payload.winner);
  const winnerKey = text(winner.candidateKey);
  const eligible = new Map(
    records(payload.eligible).map((item) => [String(item.candidateKey ?? ""), item]),
  );
  const rejected = new Map(
    records(payload.rejected).map((item) => [String(item.candidateKey ?? ""), item]),
  );
  return records(payload.candidates).map((candidate) => {
    const key = String(candidate.candidateKey ?? "");
    const source = record(candidate.source);
    const scored = eligible.get(key);
    const refused = rejected.get(key);
    const reasons = refused === undefined ? [] : strings(refused.reasons);
    const outcome: AegisCandidateRow["outcome"] =
      refused !== undefined
        ? "rejected"
        : key === winnerKey
          ? "winner"
          : scored !== undefined
            ? "eligible"
            : "unscored";
    return {
      key,
      providerId: text(candidate.providerId),
      providerModelId: text(candidate.providerModelId),
      modelVersion: text(candidate.modelVersion),
      aaModelId: text(source.aaModelId),
      aaName: text(source.aaName),
      mappingProvenance: text(candidate.mappingProvenance),
      amountUnits: integer(candidate.amountUnits),
      estimatedCompletionMs: decimalText(candidate.estimatedCompletionMs),
      completionBasis: text(candidate.estimatedCompletionMsBasis),
      intelligenceIndex: decimalText(source.intelligenceIndex),
      inputPricePerMillion: decimalText(source.inputPricePerMillion),
      outputPricePerMillion: decimalText(source.outputPricePerMillion),
      medianEndToEndSeconds: decimalText(source.medianEndToEndSeconds),
      rank: scored === undefined ? null : integer(scored.rank),
      scores: scored === undefined ? null : scores(scored.scores),
      outcome,
      rejectionReasons: reasons,
    };
  });
}

export function readAegisDecision(events: EvidenceEvent[]): AegisDecisionView | null {
  const decided = events.find((event) => event.type === "DECIDED");
  if (decided === undefined) return null;
  const payload = decided.payload as Json;
  if (payload.scoringPolicyVersion !== AEGIS_SCORING_POLICY_VERSION) return null;
  const priority = record(payload.priority);
  const weights = record(payload.weights);
  const tokenIdentity = record(payload.token);
  const estimate = record(payload.tokens);
  return {
    scoringPolicyVersion: text(payload.scoringPolicyVersion),
    priority: {
      effective: text(priority.effectivePriority),
      original: text(priority.originalPriority),
      reason: text(priority.reason),
      method: text(priority.classificationMethod),
      matchedKeywords: strings(priority.matchedKeywords),
    },
    weights: {
      price: integer(weights.price),
      completionTime: integer(weights.completionTime),
      intelligence: integer(weights.intelligence),
    },
    amountUnits: integer(payload.amountUnits),
    token: {
      name: text(tokenIdentity.name),
      symbol: text(tokenIdentity.symbol),
      decimals: integer(tokenIdentity.decimals),
      address: text(tokenIdentity.address),
      chainId: integer(tokenIdentity.chainId),
      status: text(tokenIdentity.status),
    },
    tokens: {
      estimatedInputTokens: integer(estimate.estimatedInputTokens),
      maxOutputTokens: integer(estimate.maxOutputTokens),
      estimationMethod: text(estimate.estimationMethod),
      isEstimate: estimate.isEstimate !== false,
    },
    catalogVersion: text(payload.catalogVersion),
    snapshotId: text(payload.snapshotId),
    snapshotHash: text(payload.snapshotHash),
    termsBindingHash: text(payload.termsBindingHash),
    explanation: text(payload.explanation),
    generatedExplanation: text(payload.generatedExplanation),
    winnerKey: text(record(payload.winner).candidateKey),
    rows: candidateRows(payload),
  };
}

export function readAegisSnapshot(events: EvidenceEvent[]): AegisSnapshotView | null {
  const captured = events.find((event) => event.type === "AA_SNAPSHOT_RECORDED");
  if (captured === undefined) return null;
  const payload = captured.payload as Json;
  return {
    snapshotId: text(payload.snapshotId),
    snapshotHash: text(payload.snapshotHash),
    mode: text(payload.mode),
    sourceUrl: text(payload.sourceUrl),
    attributionUrl: text(payload.attributionUrl),
    catalogVersion: text(payload.catalogVersion),
    catalogProvenance: strings(payload.catalogProvenance),
    completionBenchmark: text(payload.completionBenchmark),
    totalModelCount: integer(payload.totalModelCount),
    pageCount: integer(payload.pageCount),
    fetchedAt: captured.occurred_at,
  };
}

/**
 * The delivery of an `aa-three-factor-v1` purchase. A stored legacy delivery has no
 * execution mode and no observed duration, so it is not narrowed to this view.
 */
export function readAegisDelivery(events: EvidenceEvent[]): AegisDeliveryView | null {
  const delivered = events.find((event) => event.type === "DELIVERED");
  if (delivered === undefined) return null;
  const payload = delivered.payload as Json;
  const mode = text(payload.executionMode);
  if (mode === null) return null;
  return {
    executionMode: mode,
    observedExecutionMs: integer(payload.observedExecutionMs),
    providerId: text(payload.providerId),
    modelId: text(payload.modelId),
    modelVersion: text(payload.modelVersion),
    responseId: text(payload.responseId),
  };
}
