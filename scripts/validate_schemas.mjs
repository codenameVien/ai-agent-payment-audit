import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const commonPath = "packages/schemas/common/seller-quote.schema.json";
const aiPath = "packages/schemas/domains/ai_inference/quote-extension.schema.json";
const common = JSON.parse(await readFile(commonPath, "utf8"));
const ai = JSON.parse(await readFile(aiPath, "utf8"));

assert.equal(common.$schema, "https://json-schema.org/draft/2020-12/schema");
assert.equal(ai.$schema, "https://json-schema.org/draft/2020-12/schema");
for (const aiOnly of ["providerId", "modelId", "modelVersion", "expectedLatencyMs"]) {
  assert.equal(common.properties[aiOnly], undefined);
  assert.notEqual(ai.properties[aiOnly], undefined);
}

const snapshotPath = "packages/schemas/domains/ai_inference/aa-snapshot.schema.json";
const decisionPath = "packages/schemas/domains/ai_inference/aa-decision.schema.json";
const snapshot = JSON.parse(await readFile(snapshotPath, "utf8"));
const decision = JSON.parse(await readFile(decisionPath, "utf8"));

assert.equal(snapshot.$schema, "https://json-schema.org/draft/2020-12/schema");
assert.equal(decision.$schema, "https://json-schema.org/draft/2020-12/schema");
assert.equal(decision.properties.scoringPolicyVersion.const, "aa-three-factor-v1");
assert.equal(decision.properties.tokens.properties.estimationMethod.const, "utf8-bytes-div4-v1");
assert.deepEqual(decision.properties.priority.properties.effectivePriority.enum, [
  "default",
  "price",
  "speed",
  "intelligence",
]);
// The superseded preset names must never be accepted by the new decision schema.
for (const legacy of ["balanced", "quality", "performance"]) {
  assert.ok(!decision.properties.priority.properties.effectivePriority.enum.includes(legacy));
}
// AA numbers cross a language boundary as decimal text, never as JSON floats.
for (const field of [
  "inputPricePerMillion",
  "outputPricePerMillion",
  "medianEndToEndSeconds",
  "intelligenceIndex",
]) {
  assert.equal(snapshot.properties.models.items.properties[field].type, "string");
}
assert.equal(snapshot.properties.rawPages.items.type, "string");
assert.deepEqual(snapshot.properties.mode.enum, ["fixture", "live"]);

/**
 * Exact decimal money math with BigInt: the TypeScript side of the gateway has to reach
 * the same amountUnits as the Python buyer, so both are checked against one fixture.
 */
function scaledDecimal(text) {
  assert.match(text, /^[0-9]+(\.[0-9]+)?$/, `not decimal text: ${text}`);
  const [whole, fraction = ""] = text.split(".");
  return { value: BigInt(whole + fraction), scale: fraction.length };
}

function amountUnits({ estimatedInputTokens, maxOutputTokens, inputPricePerMillion, outputPricePerMillion }) {
  const input = scaledDecimal(inputPricePerMillion);
  const output = scaledDecimal(outputPricePerMillion);
  const scale = Math.max(input.scale, output.scale);
  const lift = (item) => item.value * 10n ** BigInt(scale - item.scale);
  const total = BigInt(estimatedInputTokens) * lift(input) + BigInt(maxOutputTokens) * lift(output);
  const divisor = 10n ** BigInt(scale);
  return (total + divisor - 1n) / divisor;
}

const pricing = JSON.parse(
  await readFile("packages/schemas/fixtures/aa/pricing-cases.json", "utf8"),
);
assert.equal(pricing.policyVersion, "aa-three-factor-v1");
assert.ok(pricing.cases.length >= 8);
for (const testCase of pricing.cases) {
  assert.equal(
    amountUnits(testCase),
    BigInt(testCase.amountUnits),
    `pricing case disagreed: ${testCase.name}`,
  );
}

console.log(`schema boundary valid; ${pricing.cases.length} pricing cases agree`);
