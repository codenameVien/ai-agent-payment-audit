import assert from "node:assert/strict";
import test from "node:test";

import { GeminiProviderAdapter } from "../src/adapters/gemini.js";
import { NemotronProviderAdapter } from "../src/adapters/nemotron.js";

test("Gemini adapter follows provider contract without exposing the API key", async () => {
  let seenUrl = "";
  let seenHeaders: HeadersInit | undefined;
  const adapter = new GeminiProviderAdapter({
    apiKey: "test-only-key",
    fetchImpl: async (input, init) => {
      seenUrl = String(input);
      seenHeaders = init?.headers;
      return Response.json({
        responseId: "gemini-response-1",
        modelVersion: "gemini-version-1",
        candidates: [{ content: { parts: [{ text: "gemini answer" }] } }],
      });
    },
  });
  const result = await adapter.generate("gemini-test", "hello");
  assert.match(seenUrl, /gemini-test:generateContent$/);
  assert.equal(new Headers(seenHeaders).get("x-goog-api-key"), "test-only-key");
  assert.equal(result.responseId, "gemini-response-1");
  assert.equal(result.text, "gemini answer");
  assert.doesNotMatch(JSON.stringify(result), /test-only-key/);
});

test("Nemotron adapter follows the same provider result contract", async () => {
  let body = "";
  const adapter = new NemotronProviderAdapter({
    apiKey: "test-only-key",
    fetchImpl: async (_input, init) => {
      body = String(init?.body);
      return Response.json({
        id: "nvidia-response-1",
        model: "nvidia/nemotron-test",
        choices: [{ message: { content: "nemotron answer" } }],
      });
    },
  });
  const result = await adapter.generate("nvidia/nemotron-test", "hello");
  assert.equal(JSON.parse(body).stream, false);
  assert.equal(result.providerId, "nemotron");
  assert.equal(result.responseId, "nvidia-response-1");
  assert.equal(result.text, "nemotron answer");
});
