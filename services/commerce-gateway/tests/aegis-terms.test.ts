import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  AegisTermsError,
  amountUnits,
  canonicalJson,
  deriveTerms,
  termsBindingHash,
  type AegisDecisionEvidence,
} from "../src/aegis/terms.js";
import { buildDecisionEvidence, TEST_MODELS, TEST_TOKEN } from "./aegis-fixtures.js";

function evidenceOf(raw: Record<string, unknown>): AegisDecisionEvidence {
  return {
    purchaseId: raw.purchase_id as string,
    decision: raw.decision as Record<string, unknown>,
    decisionEventHash: raw.decision_event_hash as string,
    snapshot: raw.snapshot as Record<string, unknown>,
    snapshotEventHash: raw.snapshot_event_hash as string,
    snapshotHash: raw.snapshot_hash as string,
  };
}

test("the money math agrees with the shared cross-language pricing fixture", async () => {
  const fixture = JSON.parse(
    await readFile(
      // Resolved from the compiled test at services/commerce-gateway/dist/tests/.
      new URL("../../../../packages/schemas/fixtures/aa/pricing-cases.json", import.meta.url),
      "utf8",
    ),
  ) as {
    cases: {
      name: string;
      estimatedInputTokens: number;
      maxOutputTokens: number;
      inputPricePerMillion: string;
      outputPricePerMillion: string;
      amountUnits: number;
    }[];
  };
  assert.ok(fixture.cases.length >= 8);
  for (const testCase of fixture.cases) {
    assert.equal(amountUnits(testCase), BigInt(testCase.amountUnits), testCase.name);
  }
});

test("canonical JSON sorts keys and refuses anything but exact integers", () => {
  assert.equal(canonicalJson({ b: 1, a: "x" }), '{"a":"x","b":1}');
  assert.equal(canonicalJson({ "\u00e9": 1, z: 2 }), '{"z":2,"é":1}');
  // A fractional or unsafe number has no single canonical form we will guess at, so an
  // amount that lost precision on the wire can never be hashed or signed.
  assert.throws(() => canonicalJson({ amountUnits: 1.5 }), AegisTermsError);
  assert.throws(() => canonicalJson({ amountUnits: 2 ** 53 }), AegisTermsError);
});

test("a terms binding above the exact integer range is refused, never rounded", () => {
  assert.throws(
    () =>
      termsBindingHash({
        purchaseId: "p",
        snapshotHash: `sha256:${"1".repeat(64)}`,
        providerId: "openai",
        providerModelId: "gpt-4.1-2025-04-14",
        modelVersion: "2025-04-14",
        amountUnits: BigInt(Number.MAX_SAFE_INTEGER) + 1n,
        recipient: TEST_MODELS.openai!.recipient,
        token: TEST_TOKEN,
      }),
    /exact|canonical/,
  );
});

test("terms are recomputed from the captured snapshot, not read from the decision", () => {
  const evidence = evidenceOf(
    buildDecisionEvidence({
      purchaseId: "purchase-1",
      model: TEST_MODELS.openai!,
      amountUnits: 619n,
    }),
  );
  const terms = deriveTerms(evidence);
  assert.equal(terms.amountUnits, 619n);
  assert.equal(terms.providerModelId, "gpt-4.1-2025-04-14");
  assert.equal(terms.recipient, TEST_MODELS.openai!.recipient);
  assert.equal(terms.aaMode, "fixture");
  assert.equal(terms.aaMappingProvenance, "fixture");
});

test("a decision edited after capture cannot produce terms", () => {
  for (const [label, overrides] of [
    ["a cheaper amount", { amountUnits: 1 }],
    ["a swapped recipient", { tamperedRecipient: "0x00000000000000000000000000000000000bad01" }],
    ["a rewritten binding", { termsBindingHash: `sha256:${"9".repeat(64)}` }],
    ["a price the snapshot never published", { snapshotInputPrice: "0.01" }],
  ] as const) {
    const evidence = evidenceOf(
      buildDecisionEvidence({
        purchaseId: "purchase-1",
        model: TEST_MODELS.openai!,
        amountUnits: 619n,
        overrides,
      }),
    );
    assert.throws(() => deriveTerms(evidence), AegisTermsError, label);
  }
});
