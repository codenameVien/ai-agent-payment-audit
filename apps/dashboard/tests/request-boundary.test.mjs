import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const requestUrl = new URL("../src/components/purchase-request.tsx", import.meta.url);
const overviewUrl = new URL("../src/components/overview.tsx", import.meta.url);
const homeUrl = new URL("../src/app/page.tsx", import.meta.url);
const dashboardUrl = new URL("../src/app/dashboard/page.tsx", import.meta.url);
const legacyUrl = new URL("../src/app/experiments/page.tsx", import.meta.url);
const navUrl = new URL("../src/components/Nav.tsx", import.meta.url);
const shellUrl = new URL("../src/components/app-shell.tsx", import.meta.url);
const statusUrl = new URL("../src/components/status.tsx", import.meta.url);
const purchaseListUrl = new URL("../src/components/purchase-list.tsx", import.meta.url);
const purchaseDetailUrl = new URL("../src/components/purchase-detail.tsx", import.meta.url);
const aegisDecisionUrl = new URL(
  "../src/features/ai-inference/aegis-decision.tsx",
  import.meta.url,
);

test("purchase request collects prompt, optional budget and priority with acknowledgement", async () => {
  const source = await readFile(requestUrl, "utf8");
  assert.match(source, /name="prompt"/);
  assert.match(source, /name="budget"/);
  assert.match(source, /name="priority"/);
  assert.match(source, /budgetUnits === null \? \{\} : \{ budget_units: budgetUnits \}/);
  assert.match(source, /disabled=\{!submittable\}/);
});

test("the request posts an aegis-aa-v1 body and omits priority when it is automatic", async () => {
  const source = await readFile(requestUrl, "utf8");
  assert.match(source, /requestSchema: AEGIS_REQUEST_SCHEMA_VERSION/);
  assert.match(source, /input\.priority === null \? \{\} : \{ priority: input\.priority \}/);
  assert.match(source, /priority === "auto" \? null : priority/);
  assert.match(source, /policy: \{\}/);
  assert.match(source, /1 PBLC 이하여야 합니다/);
  // The new surface never asks for a SIWE login and never sends the user to MetaMask.
  assert.doesNotMatch(source, /MetaMask|\/login|siwe/i);
  assert.match(source, /실제 PBLC 결제 · Mock Provider/);
  assert.match(source, /실제 x402 Facilitator 정산/);
  assert.match(source, /실제 Base Sepolia PBLC를 한 번 전송할 수 있으며/);
});

test("the request surface only ever writes its own aegis pending key", async () => {
  const source = await readFile(requestUrl, "utf8");
  const storageCalls = [...source.matchAll(/window\.localStorage\.(\w+)\(([^,)]*)/g)].map(
    (match) => [match[1], match[2].trim()],
  );
  assert.deepEqual(storageCalls, [
    ["setItem", "AEGIS_PENDING_PURCHASE_KEY"],
    ["removeItem", "AEGIS_PENDING_PURCHASE_KEY"],
  ]);
  // The keys of the superseded runtime are never named here: they are read through the
  // resume policy and only rendered as history links.
  assert.doesNotMatch(source, /pbl:(purchase-request-id|normal-experiment-purchase-id)/);
});

test("a stored id is verified before any resume and blocks the run when unverified", async () => {
  const source = await readFile(requestUrl, "utf8");
  assert.match(source, /resolvePendingRequest\(window\.localStorage, loadPurchase\)/);
  assert.match(source, /pending\?\.status === "blocked"/);
  assert.match(source, /pending\?\.status === "resume" \? pending\.pending : null/);
  // Reuse is bound to the stored request: a changed prompt, priority or budget creates
  // a new purchase instead of silently re-running the stored one.
  assert.match(source, /matchesPendingRequest\(resumable, input\)/);
  // One gate decides for both the button and the handler, and it refuses while the
  // lookup is still in flight.
  assert.match(source, /const submittable = canSubmitRequest\(pending, \{ busy, acknowledged \}\);/);
  assert.match(source, /if \(!submittable\) return;/);
  assert.match(source, /const checking = pending === null;/);
  // A re-check re-enters the checking state instead of running against a stale answer.
  assert.match(source, /setPending\(null\);\n\s*setPending\(await resolvePendingRequest/);
  assert.match(source, /pending\?\.historical\.map/);
  assert.match(source, /과거 거래 상세 보기/);
});

test("audit dashboard is read-only and legacy experiments redirects to request", async () => {
  const [overview, home, dashboard, legacy, nav, shell] = await Promise.all([
    readFile(overviewUrl, "utf8"),
    readFile(homeUrl, "utf8"),
    readFile(dashboardUrl, "utf8"),
    readFile(legacyUrl, "utf8"),
    readFile(navUrl, "utf8"),
    readFile(shellUrl, "utf8"),
  ]);
  assert.doesNotMatch(overview, /<form|PurchaseRequest|ExperimentRunner|POST \/purchases/);
  assert.match(home, /<Overview/);
  assert.match(dashboard, /<Overview/);
  assert.match(legacy, /redirect\("\/request"\)/);
  assert.doesNotMatch(nav, /\/request|\/experiments/);
  assert.match(shell, /pathname\.startsWith\("\/request"\)/);
  assert.match(shell, /Buyer Agent Request/);
});

test("purchase detail branches on the stored policy instead of one merged view", async () => {
  const [detail, decision] = await Promise.all([
    readFile(purchaseDetailUrl, "utf8"),
    readFile(aegisDecisionUrl, "utf8"),
  ]);
  assert.match(detail, /isAegisRequest\(detail\.summary\.request_summary\)/);
  assert.match(detail, /<AegisPurchaseDecision events=\{detail\.events\}/);
  // Historical purchases keep their signed-quote evidence view.
  assert.match(detail, /<AiInferencePurchaseEvidence events=\{detail\.events\}/);
  assert.match(decision, /벤치마크 참고 추정치/);
  assert.match(decision, /fixture 스냅샷의 가격·시간·성능 값은 저장된/);
  assert.match(decision, /Mock Provider와 Mock\n\s*Facilitator/);
  assert.match(decision, /관측 실행시간\(모의 실행\)/);
});

test("unresolved payments are not presented as actively auditing or paid", async () => {
  const [status, overview, purchaseList, purchaseDetail] = await Promise.all([
    readFile(statusUrl, "utf8"),
    readFile(overviewUrl, "utf8"),
    readFile(purchaseListUrl, "utf8"),
    readFile(purchaseDetailUrl, "utf8"),
  ]);
  assert.doesNotMatch(status, /진행 중/);
  assert.match(status, /PAYMENT_RECONCILIATION_REQUIRED: "결제 확인 필요"/);
  assert.match(status, /PAYMENT_FAILED: "결제 실패"/);
  assert.match(status, /"감사 미실행"/);
  assert.match(status, /const confirmed =/);
  assert.match(status, /"PAYMENT_SETTLED"/);
  assert.match(status, /confirmed \? "" : "예정 "/);
  assert.match(status, /"거래 해시 존재 · 결제 미확정"/);
  assert.match(status, /"온체인 결제 확인"/);
  assert.match(overview, /<PurchaseStatusBadge value=\{item.status\}/);
  assert.match(overview, /거래 해시 없음/);
  assert.match(purchaseList, /<PaymentAmount/);
  assert.match(purchaseDetail, /결제 결과가 확정되지 않아 감사를 실행하지 않았습니다/);
});

test("aegis settlement is never presented as an independently verified chain payment", async () => {
  const [status, overview] = await Promise.all([
    readFile(statusUrl, "utf8"),
    readFile(overviewUrl, "utf8"),
  ]);
  assert.match(status, /policy === "aegis"/);
  assert.match(status, /Facilitator 응답 기준 정산 · 모의 결제/);
  // The aegis branch must resolve on its own, without the historical on-chain wording
  // or the transaction hash the new settlement never produces.
  const aegisBranch = status.slice(
    status.indexOf('if (policy === "aegis")'),
    status.indexOf("const confirmed ="),
  );
  assert.ok(aegisBranch.length > 0);
  assert.doesNotMatch(aegisBranch, /온체인|BaseScan|transactionHash/);
  assert.match(overview, /item\.payment_status === "PAYMENT_SETTLED"/);
  assert.match(overview, /Facilitator 응답 기준/);
  assert.doesNotMatch(overview, /BASE SEPOLIA LIVE/);
  // An unqueried balance is reported as unqueried, not as zero.
  assert.match(overview, /rpc_not_configured: "잔액 조회 안 함 · RPC 미구성"/);
  assert.match(overview, /token_balance_units == null\n\s*\? "—"/);
  assert.doesNotMatch(overview, /\/login/);
});
