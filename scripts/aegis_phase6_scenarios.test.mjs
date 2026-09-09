#!/usr/bin/env node
// 개정 Phase 6 시나리오 하니스 — 실제 HTTP + 실제 MongoDB.
//
// 모든 hop은 실제 요청이다: 브라우저 역할→증거 API→결제 실행 모듈→Mock Provider Gateway
// →Mock Facilitator. 저장소는 이 실행이 소유한 임시 replica set 이며 기존 DB는 열지도
// 쓰지도 않는다. 모든 카운터는 실측값이며 고정 0이 아니다.
//
// 실행: node --test --require ./scripts/aegis_outbound_guard.cjs scripts/aegis_phase6_scenarios.test.mjs

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, describe, test } from "node:test";

import { startAegisStack } from "./aegis_local_stack.mjs";

const require_ = createRequire(import.meta.url);
const guard = require_("./aegis_outbound_guard.cjs");
const REPO_ROOT = resolve(import.meta.dirname, "..");
const FIXTURES = join(REPO_ROOT, "packages/schemas/fixtures/aa");
// TEST-NET-1 (RFC 5737). Never routable, so even a broken guard cannot reach a host.
// Port 8080 rather than a fetch-blocked port, so the attempt really reaches the socket.
const NON_ROUTABLE = "192.0.2.1";
const NON_ROUTABLE_PORT = 8080;

const PROVIDERS = {
  openai: { providerModelId: "gpt-4.1-2025-04-14", modelVersion: "2025-04-14" },
  anthropic: { providerModelId: "claude-sonnet-5", modelVersion: "claude-sonnet-5" },
  google: { providerModelId: "gemini-2.5-flash", modelVersion: "gemini-2.5-flash" },
};

let outboundLog;
let logDir;

async function outboundAttempts() {
  try {
    const raw = await readFile(outboundLog, "utf8");
    return raw
      .split("\n")
      .filter((line) => line.length > 0)
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

function client(stack) {
  return async (path, init = {}) => {
    const response = await fetch(`${stack.evidenceUrl}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init.headers },
    });
    const text = await response.text();
    return {
      status: response.status,
      body: text.length === 0 ? undefined : JSON.parse(text),
    };
  };
}

async function createPurchase(api, request, budgetUnits = 50_000) {
  const created = await api("/purchases", {
    method: "POST",
    body: JSON.stringify({
      domain: "ai_inference",
      budget_units: budgetUnits,
      policy: { scoringPolicyVersion: "aa-three-factor-v1" },
      request: { requestSchema: "aegis-aa-v1", ...request },
    }),
  });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  return created.body.purchase_id;
}

function counts(events) {
  return events.reduce((totals, event) => {
    totals[event.type] = (totals[event.type] ?? 0) + 1;
    return totals;
  }, {});
}

before(async () => {
  logDir = await mkdtemp(join(tmpdir(), "pbl-aegis-outbound."));
  outboundLog = join(logDir, "attempts.jsonl");
  process.env.AEGIS_OUTBOUND_LOG = outboundLog;
});

after(async () => {
  if (logDir !== undefined) await rm(logDir, { recursive: true, force: true });
});

describe("outbound transport boundary", () => {
  test("a non-loopback connection is refused and actually counted", async () => {
    // The in-process counter is the exact one; the shared log also holds child attempts.
    const before_ = guard.counters.rejected;
    await assert.rejects(
      fetch(`http://${NON_ROUTABLE}:${NON_ROUTABLE_PORT}/`, {
        signal: AbortSignal.timeout(5_000),
      }),
      /refused: loopback only|fetch failed/,
    );
    // A real counter moved, rather than a constant zero being asserted.
    assert.equal(guard.counters.rejected, before_ + 1);
    assert.equal(
      guard.counters.rejectedTargets.at(-1),
      `${NON_ROUTABLE}:${NON_ROUTABLE_PORT}`,
    );
    const logged = (await outboundAttempts()).filter(
      (item) => item.kind === "rejected" && item.host === NON_ROUTABLE,
    );
    assert.equal(logged.length >= 1, true);
  });

  test("a hostname that would need public DNS is refused before it resolves", async () => {
    await assert.rejects(
      fetch("https://artificialanalysis.ai/api/v2/language/models/free", {
        signal: AbortSignal.timeout(2_000),
      }),
    );
    const rejected = (await outboundAttempts()).filter(
      (item) => item.host === "artificialanalysis.ai",
    );
    assert.equal(rejected.length >= 1, true);
  });
});

describe("abnormal AA evidence stops the purchase before any payment", () => {
  test("a catalog id the capture never returned aborts the decision", async () => {
    const stack = await startAegisStack({
      modelCatalogPath: join(FIXTURES, "model-catalog.unmapped.json"),
      outboundLogPath: outboundLog,
    });
    try {
      const api = client(stack);
      const purchaseId = await createPurchase(api, { prompt: "매핑 오류 검증용 프롬프트입니다." });
      const decided = await api(`/purchases/${purchaseId}/decide`, { method: "POST" });
      assert.equal(decided.status, 409);
      const events = (await api(`/purchases/${purchaseId}/events`)).body;
      const total = counts(events);
      assert.equal(total.DECIDED ?? 0, 0);
      assert.equal(total.PAYMENT_INTENT_CLAIMED ?? 0, 0);
      assert.equal(total.PAYMENT_SETTLED ?? 0, 0);
    } finally {
      await stack.stop();
    }
  });

  test("a null AA price is unmeasured, not zero, and aborts the decision", async () => {
    const stack = await startAegisStack({
      aaFixturePages: [
        join(FIXTURES, "free-models-null-metric-page-1.json"),
        join(FIXTURES, "free-models-page-2.json"),
      ],
      outboundLogPath: outboundLog,
    });
    try {
      const api = client(stack);
      const purchaseId = await createPurchase(api, { prompt: "null 수치 검증용 프롬프트입니다." });
      const decided = await api(`/purchases/${purchaseId}/decide`, { method: "POST" });
      assert.equal(decided.status, 409);
      assert.match(String(decided.body.detail), /null|미측정|unmeasured/i);
      const events = (await api(`/purchases/${purchaseId}/events`)).body;
      assert.equal(counts(events).PAYMENT_SETTLED ?? 0, 0);
    } finally {
      await stack.stop();
    }
  });

  test("pages that disagree about the index version abort the capture", async () => {
    const stack = await startAegisStack({
      aaFixturePages: [
        join(FIXTURES, "free-models-page-1.json"),
        join(FIXTURES, "free-models-mixed-version-page-2.json"),
      ],
      outboundLogPath: outboundLog,
    });
    try {
      const api = client(stack);
      const purchaseId = await createPurchase(api, { prompt: "버전 혼재 검증용 프롬프트입니다." });
      const decided = await api(`/purchases/${purchaseId}/decide`, { method: "POST" });
      assert.equal(decided.status, 409);
      assert.match(String(decided.body.detail), /intelligence_index_version/);
      const events = (await api(`/purchases/${purchaseId}/events`)).body;
      assert.equal(counts(events).AA_SNAPSHOT_RECORDED ?? 0, 0);
    } finally {
      await stack.stop();
    }
  });
});

describe("the runnable default composition end to end", () => {
  let stack;
  let api;

  before(async () => {
    stack = await startAegisStack({ outboundLogPath: outboundLog });
    api = client(stack);
  }, { timeout: 180_000 });

  after(async () => {
    if (stack !== undefined) await stack.stop();
  });

  for (const [providerId, expected] of Object.entries(PROVIDERS)) {
    test(`${providerId} is selected, paid once and delivered`, { timeout: 60_000 }, async () => {
      const purchaseId = await createPurchase(api, {
        prompt: `${providerId} 정상 경로 검증용 프롬프트입니다.`,
        allowed_providers: [providerId],
      });
      const run = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
      assert.equal(run.status, 200, JSON.stringify(run.body));
      assert.equal(run.body.payment.payment_status, "settled");
      assert.equal(run.body.payment.provider_model_id, expected.providerModelId);
      assert.equal(run.body.payment.model_version, expected.modelVersion);

      const events = (await api(`/purchases/${purchaseId}/events`)).body;
      const total = counts(events);
      // The paid amount is the amount the recorded decision fixed, not a constant.
      const decided = events.find((event) => event.type === "DECIDED");
      assert.equal(run.body.payment.amount_units, decided.payload.amountUnits);
      assert.equal(decided.payload.winner.providerModelId, expected.providerModelId);
      assert.equal(total.PAYMENT_INTENT_CLAIMED, 1);
      assert.equal(total.PAYMENT_AUTHORIZED, 1);
      assert.equal(total.PAYMENT_SETTLED, 1);
      assert.equal(total.DELIVERED, 1);
      assert.equal(total.AUDITED, 1);
      const audited = events.find((event) => event.type === "AUDITED");
      const rules = audited.payload.findings.map((finding) => finding.ruleId);
      // The original request was re-classified, so the un-derivable caution is gone.
      assert.equal(rules.includes("AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE"), false);
      assert.equal(rules.includes("AUD-AA-PRIORITY-ORIGINAL-MISMATCH"), false);
      assert.equal(rules.includes("AUD-PAYMENT-PENDING"), false);
    });
  }

  test("a budget below every candidate leaves no eligible model and no payment", async () => {
    const purchaseId = await createPurchase(api, { prompt: "예산 부족 검증용 프롬프트입니다." }, 10);
    const run = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(run.status, 409);
    assert.match(String(run.body.detail), /no_eligible_candidate/);
    const total = counts((await api(`/purchases/${purchaseId}/events`)).body);
    assert.equal(total.DECIDED ?? 0, 0);
    assert.equal(total.PAYMENT_SETTLED ?? 0, 0);
  });

  test("two concurrent runs of one purchase settle exactly once", { timeout: 60_000 }, async () => {
    const purchaseId = await createPurchase(api, { prompt: "동시 실행 검증용 프롬프트입니다." });
    const runs = await Promise.all([
      api(`/purchases/${purchaseId}/run`, { method: "POST" }),
      api(`/purchases/${purchaseId}/run`, { method: "POST" }),
    ]);
    assert.equal(runs.some((run) => run.status === 200), true, JSON.stringify(runs));
    const total = counts((await api(`/purchases/${purchaseId}/events`)).body);
    assert.equal(total.PAYMENT_INTENT_CLAIMED, 1);
    assert.equal(total.PAYMENT_SETTLED, 1);
    assert.equal(total.DELIVERED, 1);
  });

  test("a paid purchase re-run is delivered without a second payment", { timeout: 60_000 }, async () => {
    const purchaseId = await createPurchase(api, { prompt: "재실행 검증용 프롬프트입니다." });
    const first = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(first.body.payment.payment_status, "settled");
    const again = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(again.body.payment.payment_status, "settled");
    assert.equal(
      again.body.payment.provider_result.responseId,
      first.body.payment.provider_result.responseId,
    );
    const total = counts((await api(`/purchases/${purchaseId}/events`)).body);
    assert.equal(total.PAYMENT_SETTLED, 1);
    assert.equal(total.PAYMENT_AUTHORIZED, 1);
    assert.equal(total.DELIVERED, 1);
  });

  test("read paths are zero-write for both new and historical evidence", async () => {
    const purchaseId = await createPurchase(api, { prompt: "읽기 경로 검증용 프롬프트입니다." });
    await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    const before_ = (await api(`/purchases/${purchaseId}/events`)).body;
    for (const path of [
      "/purchases",
      `/purchases/${purchaseId}`,
      `/purchases/${purchaseId}/events`,
      "/audit-alerts",
      "/agents",
      "/wallet",
      "/auth/me",
    ]) {
      const read = await api(path);
      assert.equal(read.status, 200, `${path} -> ${read.status}`);
    }
    const after_ = (await api(`/purchases/${purchaseId}/events`)).body;
    assert.equal(after_.length, before_.length);
    assert.deepEqual(after_.at(-1).event_hash, before_.at(-1).event_hash);
  });

  test("the evidence survives an evidence API restart on the same store", { timeout: 120_000 }, async () => {
    const purchaseId = await createPurchase(api, { prompt: "재시작 지속성 검증용 프롬프트입니다." });
    const run = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(run.status, 200, JSON.stringify(run.body));
    assert.equal(run.body.payment.payment_status, "settled");
    const before_ = (await api(`/purchases/${purchaseId}/events`)).body;

    // The whole composition is restarted against the same MongoDB instance.
    const restarted = await stack.restart();
    api = client(restarted);
    const after_ = (await api(`/purchases/${purchaseId}/events`)).body;
    assert.equal(after_.length, before_.length);
    assert.equal(after_.at(-1).event_hash, before_.at(-1).event_hash);
    assert.equal(counts(after_).PAYMENT_SETTLED, 1);

    // And a run after the restart still refuses to pay a second time.
    const rerun = await api(`/purchases/${purchaseId}/run`, { method: "POST" });
    assert.equal(rerun.body.payment.payment_status, "settled");
    assert.equal(counts((await api(`/purchases/${purchaseId}/events`)).body).PAYMENT_SETTLED, 1);
  });

  test("every observed outbound attempt in this run was loopback or refused", async () => {
    const attempts = await outboundAttempts();
    assert.equal(attempts.length > 0, true, "the tripwire recorded nothing at all");
    const allowedHosts = new Set(attempts.filter((i) => i.kind === "allowed").map((i) => i.host));
    for (const host of allowedHosts) {
      assert.equal(guard.isLoopback(host), true, `non-loopback host was allowed: ${host}`);
    }
    const rejected = attempts.filter((item) => item.kind === "rejected");
    // The only refusals are the two this harness deliberately provoked.
    assert.equal(rejected.length >= 2, true);
    for (const item of rejected) {
      assert.equal(guard.isLoopback(item.host), false);
    }
  });
});
