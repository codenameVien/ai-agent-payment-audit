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

test("purchase request collects prompt, optional budget and priority with acknowledgement", async () => {
  const source = await readFile(requestUrl, "utf8");
  assert.match(source, /name="prompt"/);
  assert.match(source, /name="budget"/);
  assert.match(source, /name="priority"/);
  assert.match(source, /budgetUnits === undefined/);
  assert.match(source, /disabled=\{busy \|\| !acknowledged\}/);
});

test("pending legacy and current purchase IDs are retained without creating a duplicate", async () => {
  const source = await readFile(requestUrl, "utf8");
  assert.match(source, /LEGACY_PENDING_PURCHASE_KEY/);
  assert.match(source, /localStorage\.setItem\(PENDING_PURCHASE_KEY, currentPurchaseId\)/);
  assert.match(source, /if \(currentPurchaseId === null\)/);
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
