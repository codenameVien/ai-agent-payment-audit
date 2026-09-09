import assert from "node:assert/strict";
import test from "node:test";

// `src/lib/aegis.ts` is imported directly: Node >= 22.18 strips the type annotations.
// On an older supported runtime these tests report as skipped instead of crashing the
// whole suite, and the surface assertions in request-boundary.test.mjs still run.
const stripsTypes = Boolean(process.features.typescript);
const aegis = stripsTypes ? await import("../src/lib/aegis.ts") : null;
const skip = stripsTypes ? false : "requires a Node build with TypeScript type stripping";

function event(type, payload) {
  return {
    event_id: `${type}-1`,
    sequence: 1,
    type,
    occurred_at: "2026-09-09T00:00:00Z",
    actor: { id: "buyer-agent", type: "agent" },
    payload,
    event_hash: "sha256:head",
    evidence_refs: [],
    redacted: false,
  };
}

const winnerScores = {
  completionTime: "100",
  intelligence: "98.500000000000000000",
  price: "100",
  total: "99.700000000000000000",
};

const decided = event("DECIDED", {
  amountUnits: 5250,
  scoringPolicyVersion: "aa-three-factor-v1",
  catalogVersion: "aegis-catalog-1",
  snapshotId: "doc-1",
  snapshotHash: "sha256:snapshot",
  termsBindingHash: "sha256:terms",
  explanation: "결정적 설명",
  generatedExplanation: "보조 설명",
  priority: {
    classificationMethod: "keyword-single-match-v1",
    effectivePriority: "price",
    matchedKeywords: ["싸게"],
    matchedPriorities: ["price"],
    originalPriority: null,
    reason: "keyword_match",
    weights: { completionTime: 20, intelligence: 20, price: 60 },
  },
  weights: { completionTime: 20, intelligence: 20, price: 60 },
  tokens: {
    estimatedInputTokens: 42,
    estimationMethod: "utf8-bytes-div4-v1",
    inputByteLength: 165,
    isEstimate: true,
    maxOutputTokens: 500,
  },
  token: {
    address: "0xaeg",
    chainId: 84532,
    decimals: 6,
    name: "AEGIS",
    status: "prepared",
    symbol: "AEGIS",
  },
  candidates: [
    {
      amountUnits: 5250,
      candidateKey: "openai:gpt-x:2026-01",
      estimatedCompletionMs: "12345.678",
      estimatedCompletionMsBasis: "aa-median-end-to-end-500-answer-tokens",
      mappingProvenance: "fixture",
      modelVersion: "2026-01",
      providerId: "openai",
      providerModelId: "gpt-x",
      source: {
        aaModelId: "aa-1",
        aaName: "GPT X",
        inputPricePerMillion: "2.5",
        intelligenceIndex: "41.20",
        medianEndToEndSeconds: "12.345678",
        outputPricePerMillion: "10",
      },
    },
    {
      amountUnits: 90000,
      candidateKey: "anthropic:claude-y:2026-02",
      estimatedCompletionMs: "22000",
      mappingProvenance: "configured",
      modelVersion: "2026-02",
      providerId: "anthropic",
      providerModelId: "claude-y",
      source: { aaModelId: "aa-2", intelligenceIndex: "55" },
    },
    {
      amountUnits: 6000,
      candidateKey: "google:gemini-z:2026-03",
      estimatedCompletionMs: "9000",
      modelVersion: "2026-03",
      providerId: "google",
      providerModelId: "gemini-z",
      source: { aaModelId: "aa-3", intelligenceIndex: "40" },
    },
  ],
  eligible: [
    {
      amountUnits: 5250,
      candidateKey: "openai:gpt-x:2026-01",
      rank: 1,
      scores: winnerScores,
    },
  ],
  rejected: [
    {
      candidateKey: "anthropic:claude-y:2026-02",
      modelVersion: "2026-02",
      providerId: "anthropic",
      providerModelId: "claude-y",
      reasons: ["over_budget", "completion_time_limit"],
    },
  ],
  winner: { amountUnits: 5250, candidateKey: "openai:gpt-x:2026-01", rank: 1, scores: winnerScores },
});

const snapshotRecorded = event("AA_SNAPSHOT_RECORDED", {
  attributionUrl: "https://artificialanalysis.ai/",
  catalogProvenance: ["fixture"],
  catalogVersion: "aegis-catalog-1",
  completionBenchmark: "aa-median-end-to-end-500-answer-tokens",
  mode: "fixture",
  pageCount: 1,
  snapshotHash: "sha256:snapshot",
  snapshotId: "doc-1",
  sourceUrl: "https://artificialanalysis.ai/api/v2/language/models/free",
  totalModelCount: 3,
});

test("stored request schema discriminates the new policy from history", { skip }, () => {
  assert.equal(aegis.isAegisRequest({ request_schema_version: "aegis-aa-v1" }), true);
  assert.equal(aegis.isAegisRequest({ priority: "balanced" }), false);
  assert.equal(aegis.isAegisRequest(undefined), false);
  assert.equal(aegis.isAegisRequest(null), false);
});

test("a superseded decision is never read as an aa-three-factor-v1 decision", { skip }, () => {
  const legacy = event("DECIDED", {
    winner: { quote_id: "q-1" },
    rejected: [{ quote_id: "q-2", reasons: ["score"] }],
    explanation: "과거 정책 설명",
  });
  assert.equal(aegis.readAegisDecision([legacy]), null);
  assert.equal(aegis.readAegisDecision([]), null);
});

test("candidates, scores and rejections merge into one ordered comparison", { skip }, () => {
  const decision = aegis.readAegisDecision([snapshotRecorded, decided]);
  assert.notEqual(decision, null);
  assert.equal(decision.rows.length, 3);

  const [winner, refused, unscored] = decision.rows;
  assert.equal(winner.outcome, "winner");
  assert.equal(winner.rank, 1);
  assert.deepEqual(winner.rejectionReasons, []);
  assert.equal(refused.outcome, "rejected");
  assert.deepEqual(refused.rejectionReasons, ["over_budget", "completion_time_limit"]);
  assert.equal(refused.scores, null);
  assert.equal(refused.rank, null);
  // A candidate that is neither scored nor rejected is disclosed, never silently ranked.
  assert.equal(unscored.outcome, "unscored");
  assert.equal(unscored.rank, null);
});

test("stored decimal evidence is displayed exactly as it was recorded", { skip }, () => {
  const decision = aegis.readAegisDecision([decided]);
  const winner = decision.rows[0];
  assert.equal(winner.estimatedCompletionMs, "12345.678");
  assert.equal(winner.intelligenceIndex, "41.20");
  assert.equal(winner.inputPricePerMillion, "2.5");
  assert.equal(winner.scores.total, "99.700000000000000000");
  assert.equal(decision.amountUnits, 5250);
  assert.equal(decision.token.symbol, "AEGIS");
  assert.equal(decision.token.decimals, 6);
  assert.equal(decision.token.status, "prepared");
  assert.equal(decision.tokens.isEstimate, true);
  assert.equal(decision.priority.effective, "price");
  assert.equal(decision.priority.original, null);
  assert.equal(decision.priority.reason, "keyword_match");
  assert.deepEqual(decision.priority.matchedKeywords, ["싸게"]);
  assert.equal(decision.weights.price, 60);
});

test("snapshot provenance and mock execution facts come from their own events", { skip }, () => {
  const snapshot = aegis.readAegisSnapshot([snapshotRecorded, decided]);
  assert.equal(snapshot.mode, "fixture");
  assert.deepEqual(snapshot.catalogProvenance, ["fixture"]);
  assert.equal(snapshot.totalModelCount, 3);
  assert.equal(aegis.readAegisSnapshot([decided]), null);

  const delivered = event("DELIVERED", {
    executionMode: "mock",
    modelId: "gpt-x",
    modelVersion: "2026-01",
    observedExecutionMs: 1234,
    providerId: "openai",
    responseId: "resp-1",
  });
  assert.equal(aegis.readAegisDelivery([delivered]).observedExecutionMs, 1234);
  assert.equal(aegis.readAegisDelivery([delivered]).executionMode, "mock");
  // A stored PBLC delivery has no execution mode and must not be shown as a mock run.
  const legacyDelivery = event("DELIVERED", {
    sellerAgentId: "seller-1",
    providerId: "openai",
    responseId: "resp-0",
  });
  assert.equal(aegis.readAegisDelivery([legacyDelivery]), null);
});

test("display shortening truncates decimals instead of rounding them", { skip }, () => {
  assert.equal(aegis.shortDecimal("99.999"), "99.99");
  assert.equal(aegis.shortDecimal("100.000000000000000000"), "100");
  assert.equal(aegis.shortDecimal("12345.678", 0), "12345");
  assert.equal(aegis.shortDecimal("41.20"), "41.2");
  assert.equal(aegis.shortDecimal(null), "—");
});

test("rejection reasons are labelled and unknown codes stay visible", { skip }, () => {
  assert.equal(aegis.describeRejection(["over_budget"]), "예산 초과");
  assert.equal(
    aegis.describeRejection(["missing_capability", "provider_not_allowed"]),
    "필수 기능 미지원 · 허용되지 않은 제공자",
  );
  assert.equal(aegis.describeRejection(["future_code"]), "future_code");
  assert.equal(aegis.describeRejection([]), "사유가 기록되지 않았습니다");
});

test("the request form offers automatic classification plus the four fixed priorities", { skip }, () => {
  const values = aegis.AEGIS_PRIORITY_OPTIONS.map((option) => option.value);
  assert.deepEqual(values, ["auto", "default", "price", "speed", "intelligence"]);
  assert.equal(values[0], "auto");
});
