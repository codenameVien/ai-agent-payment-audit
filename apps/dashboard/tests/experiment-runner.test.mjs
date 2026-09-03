import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const runnerUrl = new URL("../src/components/experiment-runner.tsx", import.meta.url);
const navUrl = new URL("../src/components/Nav.tsx", import.meta.url);
const shellUrl = new URL("../src/components/app-shell.tsx", import.meta.url);

test("normal experiment fixes the real-payment ceiling and requires acknowledgement", async () => {
  const source = await readFile(runnerUrl, "utf8");
  assert.match(source, /NORMAL_BUDGET_UNITS = 100_000/);
  assert.match(source, /budget_units: NORMAL_BUDGET_UNITS/);
  assert.match(source, /disabled=\{busy \|\| !acknowledged\}/);
  assert.doesNotMatch(source, /name="budget"/);
});

test("created purchase is retained across tabs so a retry does not create another purchase", async () => {
  const source = await readFile(runnerUrl, "utf8");
  assert.match(source, /localStorage\.setItem\(PENDING_PURCHASE_KEY, currentPurchaseId\)/);
  assert.match(source, /localStorage\.getItem\(PENDING_PURCHASE_KEY\)/);
  assert.match(source, /if \(currentPurchaseId === null\)/);
});

test("runner is absent from dashboard navigation and uses an operator shell", async () => {
  const nav = await readFile(navUrl, "utf8");
  const shell = await readFile(shellUrl, "utf8");
  assert.doesNotMatch(nav, /\/experiments/);
  assert.match(shell, /pathname\.startsWith\("\/experiments"\)/);
  assert.match(shell, /Payment Experiment Runner/);
});
