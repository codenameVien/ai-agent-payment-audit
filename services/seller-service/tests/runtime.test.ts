import assert from "node:assert/strict";
import test from "node:test";

import { createSellerTransport, EvidenceQuoteTermsReader } from "../src/main.js";

const ADDRESS = "0x0000000000000000000000000000000000000001";
const PRIVATE_KEY = `0x${"11".repeat(32)}`;

test("runtime assembly exposes signed quotes and a Facilitator-backed 402 challenge", async () => {
  const transport = createSellerTransport({
    PROVIDER_ID: "gemini",
    PROVIDER_API_KEY: "local-test-key",
    SELLER_PRIVATE_KEY: PRIVATE_KEY,
    SELLER_AGENT_ID: "seller-gemini",
    ERC8004_AGENT_ID: "7",
    MODEL_ID: "gemini-test",
    MODEL_PRICE_UNITS: "100000",
    TOKEN_ADDRESS: ADDRESS,
    PAY_TO_ADDRESS: ADDRESS,
    QUOTE_VERIFYING_CONTRACT: ADDRESS,
    FACILITATOR_URL: "https://facilitator.test",
    EVIDENCE_API_URL: "http://evidence.test",
    INTERNAL_SERVICE_TOKEN: "internal-token",
  });
  const quoteResponse = await transport.handle(new Request("http://seller/internal/quotes", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: "Bearer internal-token",
    },
    body: JSON.stringify({
      purchaseId: "purchase-runtime",
      requestId: "request-runtime",
      minInputLimit: 1,
      minOutputLimit: 1,
    }),
  }));
  assert.equal(quoteResponse.status, 200);
  const signed = await quoteResponse.json() as {
    quote: { quoteId: string; erc8004AgentId: string };
    signature: string;
  };
  assert.equal(signed.quote.erc8004AgentId, "7");
  assert.match(signed.signature, /^0x[0-9a-f]+$/);

  const challenge = await transport.handle(new Request(
    `http://seller/v1/inference?purchaseId=purchase-runtime&quoteId=${signed.quote.quoteId}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt: "hello" }),
    },
  ));
  assert.equal(challenge.status, 402);
  assert.ok(challenge.headers.get("PAYMENT-REQUIRED"));
});

test("seller restart recovers signed quote terms from authenticated evidence API", async () => {
  let authorization = "";
  const reader = new EvidenceQuoteTermsReader(
    { async read() { return null; } },
    {
      EVIDENCE_API_URL: "http://evidence.test/",
      INTERNAL_SERVICE_TOKEN: "internal-token",
    },
    async (input, init) => {
      authorization = new Headers(init?.headers).get("authorization") ?? "";
      assert.equal(
        String(input),
        "http://evidence.test/internal/evidence/purchases/purchase-1/seller-quotes/quote-1",
      );
      return Response.json({
        model_id: "gemini-test",
        amount_units: 100000,
        token: ADDRESS,
        pay_to: ADDRESS,
        expires_at: "2027-01-01T00:00:00Z",
      });
    },
  );
  const recovered = await reader.read("purchase-1", "quote-1");
  assert.equal(authorization, "Bearer internal-token");
  assert.equal(recovered?.modelId, "gemini-test");
  assert.equal(recovered?.amount, 100000n);
});
