// Local-only browser smoke. Never clicks purchase execute or allows write API calls.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
const require = createRequire(import.meta.url);
const modulePath = process.env.PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(modulePath);
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const origin = process.env.DASHBOARD_ORIGIN || "http://127.0.0.1:3100";
  assert.equal(origin, "http://127.0.0.1:3100", "only the dedicated isolated preview is allowed");
  let writes = 0;
  let allowMockPurchase = false;
  await page.route("**/*", async route => {
    const request = route.request();
    if (["POST", "PUT", "DELETE", "PATCH"].includes(request.method())) {
      writes++;
      if (allowMockPurchase && request.method() === "POST" && request.url().startsWith(`${origin}/backend/purchases`)) await route.continue();
      else await route.abort();
    } else await route.continue();
  });
  await page.goto(`${origin}/request`);
  await page.getByRole("textbox").first().fill("비용을 최소화해서 문서를 요약해줘.");
  await page.getByRole("button", { name: "메시지 보내기" }).click();
  await page.getByText("비용을 최소화해서 문서를 요약해줘.", { exact: true }).first().waitFor();
  assert.equal(writes, 0, "sending chat must never write purchase API");
  await mkdir("docs/images", { recursive: true });
  await page.screenshot({ path: "docs/images/chat-request.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "mobile overflow");
  console.log(JSON.stringify({ chatSent: true, writeRequests: writes, mobileOverflow: false, screenshot: "docs/images/chat-request.png" }));
  if (process.argv.includes("--execute-mock")) {
    const health = await (await page.request.get(`${origin}/backend/health`)).json();
    assert.equal(health.execution_mode, "mock", "refuse live settlement in browser smoke");
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.getByRole("combobox").selectOption("price");
    await page.getByRole("checkbox").check();
    allowMockPurchase = true;
    await page.getByRole("button", { name: "명시적으로 동의하고 구매 에이전트 실행" }).click();
    await page.waitForURL(/\/purchases\/[^/]+$/, { timeout: 150000 });
    await page.getByRole("heading", { name: "Qwen 관찰 · 블록체인 체크포인트" }).waitFor();
    assert.equal(await page.getByText("Mock 체크포인트 · 블록체인 미기록", { exact: true }).count(), 2);
    await page.getByRole("heading", { name: "Qwen 관찰 · 블록체인 체크포인트" }).locator("..").screenshot({ path: "docs/images/decision-observer-result.png" });
    console.log(JSON.stringify({ mockPurchaseCompleted: true, writes, detail: page.url(), checkpoints: 2 }));
  }
} finally { await browser.close(); }
