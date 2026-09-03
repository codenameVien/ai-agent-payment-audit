import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const runnerUrl = new URL("../src/components/experiment-runner.tsx", import.meta.url);
const navUrl = new URL("../src/components/Nav.tsx", import.meta.url);

test("normal experiment fixes the real-payment ceiling and requires acknowledgement", async () => {
  const source = await readFile(runnerUrl, "utf8");
  assert.match(source, /NORMAL_BUDGET_UNITS = 100_000/);
  assert.match(source, /budget_units: NORMAL_BUDGET_UNITS/);
  assert.match(source, /disabled=\{busy \|\| !acknowledged\}/);
  assert.doesNotMatch(source, /name="budget"/);
});

test("created purchase is retained so a retry does not create another purchase", async () => {
  const source = await readFile(runnerUrl, "utf8");
  assert.match(source, /sessionStorage\.setItem\(PENDING_PURCHASE_KEY, currentPurchaseId\)/);
  assert.match(source, /if \(currentPurchaseId === null\)/);
});

test("runner is visibly separated from the read-only dashboard", async () => {
  const source = await readFile(navUrl, "utf8");
  assert.match(source, /href="\/experiments">실험 실행/);
});
