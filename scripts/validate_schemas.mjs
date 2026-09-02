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

console.log("schema boundary valid");
