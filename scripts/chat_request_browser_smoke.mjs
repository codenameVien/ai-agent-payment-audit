// Default: intercepted backend fixtures, zero DB/LLM/payment writes.
// --execute-mock additionally makes two purchases on the isolated Mock preview.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const origin = process.env.DASHBOARD_ORIGIN || "http://127.0.0.1:3100";
assert.equal(origin, "http://127.0.0.1:3100", "only the dedicated preview is allowed");
const key = "aegis:token-v1:purchase-request-id";
const token = "0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b";
const prompt = "비용을 최소화해서 문서를 요약해줘.";
const hash = value => "sha256:" + createHash("sha256").update(value).digest("hex");
const browser = await chromium.launch({ headless: true });
const errors = [];
const confirm = page => page.getByRole("button", { name: /^동의하고/ }).last();
const cards = page => page.getByTestId("purchase-card");
const results = page => page.getByTestId("purchase-result");
const composer = page => page.getByRole("textbox", { name: "메시지", exact: true });
async function send(page, message = prompt) {
  await composer(page).fill(message);
  await page.getByRole("button", { name: "메시지 보내기", exact: true }).click();
}
async function result(page, count) {
  await results(page).nth(count - 1).waitFor({ timeout: 150000 });
  assert.equal(new URL(page.url()).pathname, "/request", "results stay in chat");
}
function detail(id, body, completed = false) {
  return {
    summary: { purchase_id: id, created_at: "2026-09-12T00:00:00Z", domain: "ai_inference",
      request_summary: { request_schema_version: "aegis-aa-v1", prompt_hash: hash(body.request.prompt),
        original_priority: body.request.priority ?? null, effective_priority: "price" },
      status: completed ? "AUDITED" : "REQUESTED", amount_units: completed ? 8200 : null,
      token, transaction_hash: null, audit_severity: completed ? "NORMAL" : null, finding_count: 0,
      lifecycle_status: completed ? "DELIVERED" : "REQUESTED",
      payment_status: completed ? "PAYMENT_SETTLED" : "PAYMENT_NOT_STARTED",
      audit_status: completed ? "AUDITED" : "PENDING_AUDIT", audit_covers_head: completed },
    events: [{ event_id: randomUUID(), sequence: 1, type: "REQUESTED", occurred_at: "2026-09-12T00:00:00Z",
      actor: {}, payload: { budgetUnits: body.budget_units ?? 1000000 }, event_hash: hash("requested"),
      evidence_refs: [], redacted: false }],
    audit: completed ? { report_id: randomUUID(), purchase_id: id, severity: "NORMAL",
      findings: [], evidence_head_event_hash: hash("head"), audit_bundle_hash: hash("audit") } : null,
  };
}
async function fixture({ mode = "mock", failures = 0, stored = null, lookupFails = false, healthFails = false, hangs = null, paymentStatus = "settled" } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  if (stored) await context.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key, id: stored.id });
  const page = await context.newPage();
  page.on("pageerror", e => errors.push(e.message));
  const records = new Map(stored ? [[stored.id, { body: stored.body, completed: stored.completed, paymentStatus: stored.paymentStatus }]] : []);
  const calls = [];
  const reads = [];
  const state = { lookupFails, healthFails, failures, hangs };
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/backend/, "");
    const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.origin !== origin || !url.pathname.startsWith("/backend/")) {
      if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) return route.abort();
      return route.continue();
    }
    if (request.method() === "GET") reads.push(path);
    // Intentionally unanswered GET: the UI must time out, not unlock payment or hang forever.
    if (state.hangs === "health" && path === "/health") return;
    if (state.hangs === "pending" && /^\/purchases\/[^/]+$/.test(path)) return;
    if (path === "/health") return reply(state.healthFails ? {} : { execution_mode: mode, token_symbol: "AEGIS" }, state.healthFails ? 503 : 200);
    if (request.method() === "POST") {
      calls.push({ path, body: request.postData() ? request.postDataJSON() : null });
      if (path === "/purchases") {
        const id = randomUUID();
        records.set(id, { body: request.postDataJSON(), completed: false });
        return reply({ purchase_id: id });
      }
      const match = path.match(/^\/purchases\/([^/]+)\/run$/);
      assert.ok(match, "unexpected write " + path);
      const record = records.get(match[1]);
      assert.ok(record);
      if (state.failures-- > 0) return reply({ detail: "Mock injected retryable failure" }, 502);
      record.completed = paymentStatus === "settled";
      return reply({ purchase_id: match[1], payment: {
        purchase_id: match[1], state: paymentStatus === "settled" ? "SETTLED" : "UNKNOWN", payment_status: paymentStatus, amount_units: 8200, token,
        provider_id: "openai", provider_model_id: "gpt-4.1-mini-2025-04-14", model_version: "2025-04-14",
        execution_mode: mode, verification_basis: "facilitator_response", settlement_reference: null,
        provider_result: { executionMode: "mock", text: "[Mock Provider 실행] 브라우저 경계 테스트용 모의 결과입니다." },
      }, audit: detail(match[1], record.body, true).audit });
    }
    const match = path.match(/^\/purchases\/([^/]+)$/);
    if (match && records.has(match[1])) {
      const record = records.get(match[1]);
      const saved = detail(match[1], record.body, record.completed);
      if (record.paymentStatus) saved.summary.payment_status = record.paymentStatus;
      return reply(state.lookupFails ? {} : saved, state.lookupFails ? 503 : 200);
    }
    return reply({}, 404);
  });
  await page.goto(origin + "/request");
  return { context, page, calls, reads, records, state };
}
try {
  {
    const { context, page, calls } = await fixture();
    await composer(page).fill(prompt);
    await composer(page).dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
    assert.equal(await cards(page).count(), 0, "IME must not send");
    await composer(page).press("Shift+Enter");
    assert.equal(await cards(page).count(), 0, "Shift+Enter is a newline");
    await composer(page).fill(prompt);
    await composer(page).press("Enter");
    await cards(page).first().waitFor();
    assert.equal(calls.length, 0, "chat send never creates a purchase");
    await composer(page).fill("아직 전송하지 않은 다음 요청");
    assert.equal(await page.getByText(/작성 중인 메시지를 먼저/).count(), 0);
    await page.waitForFunction(() => {
      const b = [...document.querySelectorAll("button")].find(b => b.textContent.startsWith("동의하고"));
      return b && !b.disabled;
    });
    await confirm(page).evaluate(button => { button.click(); button.click(); });
    await result(page, 1);
    assert.equal(calls.length, 2, "same-tick double click = one create/run pair");
    assert.equal(calls[0].body.request.prompt, prompt, "unsent text cannot change the sent card");
    await send(page);
    await confirm(page).click();
    await result(page, 2);
    assert.equal(calls.length, 4);
    assert.equal(calls[2].body.request.prompt, prompt, "never aggregate earlier purchases");
    assert.notEqual(calls[1].path, calls[3].path, "explicit repeated message has a new id");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "mobile overflow");
    const inputBox = await composer(page).boundingBox();
    assert.ok(inputBox.y >= 0 && inputBox.y + inputBox.height <= 844, "composer remains visible after repeated purchases");
    await page.getByRole("button", { name: "새 대화", exact: true }).click();
    assert.equal(await cards(page).count(), 0);
    assert.equal(calls.length, 4, "new conversation only clears the visible chat");
    console.log("ok - send/IME/unsent draft/double click/repeated purchase/mobile (fixtures)");
    await context.close();
  }
  {
    const { context, page, calls } = await fixture({ failures: 1 });
    await send(page);
    await confirm(page).click();
    const retry = page.getByRole("button", { name: "같은 요청 다시 시도", exact: true });
    await retry.waitFor();
    await retry.click();
    await result(page, 1);
    assert.equal(calls.filter(c => c.path === "/purchases").length, 1);
    assert.equal(calls[1].path, calls[2].path);
    console.log("ok - failed run retries same purchaseId (fixture)");
    await context.close();
  }
  {
    const oldId = randomUUID();
    const { context, page, calls } = await fixture({ stored: { id: oldId, body: { request: { prompt } }, completed: true } });
    await send(page);
    await confirm(page).click();
    await result(page, 1);
    assert.equal(calls[0].path, "/purchases");
    assert.ok(!calls[1].path.includes(oldId));
    console.log("ok - completed persisted purchase allows new explicit request (fixture)");
    await context.close();
  }
  for (const scenario of ["pending", "health"]) {
    const oldId = randomUUID();
    const { context, page, calls, reads, state } = await fixture(scenario === "pending" ? {
      stored: { id: oldId, body: { request: { prompt } }, completed: false }, lookupFails: true,
    } : { healthFails: true });
    await send(page);
    await composer(page).fill("복구 중에도 보존할 다음 초안");
    await confirm(page).waitFor();
    const notice = cards(page).last().getByTestId("purchase-readiness");
    const retry = notice.getByRole("button", { name: "연결 다시 확인", exact: true });
    await retry.waitFor();
    assert.equal(await confirm(page).isDisabled(), true);
    assert.ok((await notice.innerText()).length > 20, "reason is beside the blocked button");
    assert.equal(await confirm(page).getAttribute("aria-describedby"), await notice.getAttribute("id"));
    if (scenario === "health") assert.equal(await page.getByText("Mock 결제에서는 토큰을 전송하지 않습니다.", { exact: true }).count(), 0, "unknown mode is never disclosed as Mock");
    assert.equal(calls.length, 0);
    state.lookupFails = false;
    state.healthFails = false;
    const before = reads.length;
    await retry.evaluate(button => { button.click(); button.click(); });
    await page.waitForFunction(() => !document.querySelector("[data-purchase-confirm]").disabled);
    assert.equal(reads.slice(before).filter(path => path === "/health").length, 1, "double retry coalesces health reads");
    assert.equal(await cards(page).count(), 1, "recovery keeps sent card");
    assert.equal(await composer(page).inputValue(), "복구 중에도 보존할 다음 초안");
    assert.equal(calls.length, 0, "readiness recovery never auto-creates/runs");
    if (scenario === "pending") assert.equal(await page.evaluate(key => localStorage.getItem(key), key), oldId);
    await confirm(page).click();
    await result(page, 1);
    assert.equal(calls.filter(c => c.path === "/purchases").length, scenario === "pending" ? 0 : 1);
    if (scenario === "pending") assert.equal(calls[0].path, `/purchases/${oldId}/run`);
    console.log("ok - " + scenario + " failure -> read-only recovery -> explicit purchase, card/draft/id preserved (fixture)");
    await context.close();
  }
  for (const hangs of ["health", "pending"]) {
    const { context, page, calls, state } = await fixture({ hangs, stored: hangs === "pending" ? { id: randomUUID(), body: { request: { prompt } }, completed: false } : null });
    await send(page);
    const notice = cards(page).last().getByTestId("purchase-readiness");
    await notice.waitFor();
    assert.equal(await confirm(page).isDisabled(), true);
    const retry = notice.getByRole("button", { name: "연결 다시 확인", exact: true });
    await retry.waitFor({ timeout: 12000 });
    await page.waitForFunction(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent === "연결 다시 확인");
      return button && !button.disabled;
    }, null, { timeout: 12000 });
    assert.equal(await confirm(page).isDisabled(), true, "timeout stays fail-closed");
    state.hangs = null;
    await retry.click();
    await page.waitForFunction(() => !document.querySelector("[data-purchase-confirm]").disabled);
    assert.equal(calls.length, 0);
    console.log("ok - hanging " + hangs + " GET times out and recovers without payment (fixture)");
    await context.close();
  }
  {
    const oldId = randomUUID();
    const { context, page, records, calls } = await fixture({ stored: { id: oldId, body: { request: { prompt } }, completed: false }, lookupFails: true });
    records.delete(oldId);
    await send(page);
    const notice = cards(page).last().getByTestId("purchase-readiness");
    await notice.getByRole("button", { name: "연결 다시 확인", exact: true }).click();
    await notice.getByRole("link").waitFor();
    assert.equal(await confirm(page).isDisabled(), true, "404 is not permission to forget an unknown payment");
    assert.equal(await page.evaluate(key => localStorage.getItem(key), key), oldId);
    assert.ok((await notice.getByRole("link").getAttribute("href")).includes(oldId));
    assert.equal(calls.length, 0);
    console.log("ok - missing stored purchase stays visible/preserved, not silently bypassed (fixture)");
    await context.close();
  }
  {
    const { context, page, calls } = await fixture();
    await page.addInitScript(() => {
      window.blockDemoStorage = true;
      const getItem = Storage.prototype.getItem;
      Storage.prototype.getItem = function(key) {
        if (window.blockDemoStorage) throw new DOMException("Storage disabled for test", "SecurityError");
        return getItem.call(this, key);
      };
    });
    await page.reload();
    await send(page);
    const notice = cards(page).last().getByTestId("purchase-readiness");
    const retry = notice.getByRole("button", { name: "연결 다시 확인", exact: true });
    await retry.waitFor();
    assert.equal(await confirm(page).isDisabled(), true);
    await page.evaluate(() => { window.blockDemoStorage = false; });
    await retry.click();
    await page.waitForFunction(() => !document.querySelector("[data-purchase-confirm]").disabled);
    assert.equal(calls.length, 0);
    console.log("ok - storage access failure becomes recoverable without dropping records (fixture)");
    await context.close();
  }
  for (const paymentStatus of ["unknown", "failed", "settled_delivery_failed"]) {
    const { context, page, calls } = await fixture({ paymentStatus });
    await send(page);
    await confirm(page).click();
    await page.locator('[data-testid="purchase-card"][data-status="error"]').waitFor();
    assert.equal(await results(page).count(), 0, "HTTP 200 is not necessarily payment/delivery success");
    assert.equal(calls.length, 2);
    assert.ok(await page.evaluate(key => localStorage.getItem(key), key), "unresolved result retains the pending id");
    console.log("ok - HTTP 200 " + paymentStatus + " is not success (fixture)");
    await context.close();
  }
  {
    const { context, page, calls } = await fixture({ failures: 1 });
    await send(page);
    await confirm(page).click();
    await page.getByRole("button", { name: "같은 요청 다시 시도", exact: true }).waitFor();
    await send(page, "예산을 바꾼 새 요청입니다.");
    await confirm(page).click();
    await result(page, 1);
    assert.equal(calls.filter(c => c.path === "/purchases").length, 2, "a pre-payment refusal cannot permanently lock new requests");
    console.log("ok - a pre-payment failure allows a changed new request (fixture)");
    await context.close();
  }
  {
    const { context, page, calls } = await fixture({ stored: { id: randomUUID(), body: { request: { prompt } }, completed: false, paymentStatus: "PAYMENT_CONFIRMATION_UNKNOWN" } });
    await send(page, "미확정 정산을 우회하는 다른 요청");
    await confirm(page).click();
    await page.locator('[data-testid="purchase-card"][data-status="error"]').waitFor();
    assert.equal(calls.length, 0, "ambiguous settlement must not be bypassed by a changed prompt");
    console.log("ok - changed prompt cannot bypass unknown settlement (fixture)");
    await context.close();
  }
  {
    const { context, page, calls } = await fixture({ mode: "live" });
    await send(page);
    await page.getByText(/실제 Base Sepolia/).first().waitFor();
    assert.equal(calls.length, 0, "live-mode message cannot auto-pay");
    console.log("ok - live disclosure without submission (fixture)");
    await context.close();
  }
  if (process.argv.includes("--execute-mock")) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", e => errors.push(e.message));
    const health = await page.request.get(origin + "/backend/health");
    assert.ok(health.ok());
    assert.equal((await health.json()).execution_mode, "mock", "refuse live settlement");
    let writes = 0;
    let allowed = false;
    await page.route("**/*", async route => {
      const r = route.request();
      if (["POST", "PUT", "DELETE", "PATCH"].includes(r.method())) {
        writes++;
        if (!allowed || r.method() !== "POST" || !r.url().startsWith(origin + "/backend/purchases")) return route.abort();
      }
      return route.continue();
    });
    await page.goto(origin + "/request");
    await send(page);
    assert.equal(writes, 0);
    await mkdir("docs/images", { recursive: true });
    await page.screenshot({ path: "docs/images/chat-request.png", fullPage: true });
    allowed = true;
    await confirm(page).click();
    await result(page, 1);
    await send(page);
    await confirm(page).click();
    await result(page, 2);
    assert.equal(writes, 4, "two requests = two create/run pairs");
    const ids = await cards(page).evaluateAll(elements => elements.map(e => e.dataset.purchaseId).filter(Boolean));
    assert.equal(new Set(ids).size, 2);
    for (const id of ids) {
      const response = await page.request.get(origin + "/backend/purchases/" + encodeURIComponent(id));
      assert.ok(response.ok());
      const saved = await response.json();
      assert.equal(saved.events.filter(e => e.type === "PAYMENT_SETTLED").length, 1);
      assert.equal(saved.events.filter(e => e.type === "CHECKPOINT_RECORDED").length, 2);
      assert.equal(saved.summary.payment_status, "PAYMENT_SETTLED");
      assert.ok(saved.audit);
    }
    await page.screenshot({ path: "/private/tmp/aegis-chat-repeat-results.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    console.log(JSON.stringify({ realLocalBrowser: true, mockPurchases: ids, writes, settlementsPerPurchase: 1, checkpointsPerPurchase: 2, mobileOverflow: false, url: page.url() }));
    await context.close();
  }
  assert.deepEqual(errors, [], "browser runtime errors");
} finally { await browser.close(); }
