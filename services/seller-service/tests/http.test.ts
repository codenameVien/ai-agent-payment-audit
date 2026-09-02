import assert from "node:assert/strict";
import test from "node:test";

import type { QuoteRequest, SignedSellerQuote } from "../src/contracts.js";
import { SellerHttpTransport } from "../src/http.js";
import { FacilitatorPaymentGate, Permit2ChallengeProvider } from "../src/x402.js";

const fakeApplication = {
  async quote(request: QuoteRequest): Promise<SignedSellerQuote> {
    return {
      quote: {
        quoteId: "quote-1",
        purchaseId: request.purchaseId,
        sellerAgentId: "gemini-agent",
        erc8004AgentId: 1n,
        providerId: "gemini",
        modelId: "gemini-test",
        modelVersion: "v1",
        amount: 100_000n,
        token: "0x0000000000000000000000000000000000000002",
        payTo: "0x0000000000000000000000000000000000000003",
        expectedLatencyMs: 700n,
        inputLimit: 8_000n,
        outputLimit: 2_000n,
        expiresAt: 1_800_000_120n,
        quoteNonce: 41n,
        counterofferOf: "",
        available: true,
        counterofferReason: "",
      },
      signature: "0x1234",
      signer: "0x0000000000000000000000000000000000000003",
    };
  },
  async paidInference(): Promise<never> {
    throw new Error("payment required");
  },
};

test("health reports provider state without key material", async () => {
  const response = await new SellerHttpTransport(fakeApplication, "gemini").handle(
    new Request("http://seller.local/health"),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: "ok", providerId: "gemini" });
});

test("failed settlement still returns PAYMENT-RESPONSE with its transaction hash", async () => {
  const transaction = `0x${"cd".repeat(32)}`;
  const application = {
    quote: fakeApplication.quote,
    async paidInference() {
      return {
        result: {
          providerId: "gemini",
          modelId: "gemini-test",
          modelVersion: "v1",
          responseId: "response-1",
          text: "must-not-be-delivered",
        },
        settlement: {
          success: false,
          transaction,
          network: "eip155:84532",
          errorReason: "settlement reverted",
        },
      };
    },
  };
  const response = await new SellerHttpTransport(application, "gemini").handle(
    new Request("http://seller.local/v1/inference?purchaseId=purchase-1&quoteId=quote-1", {
      method: "POST",
      headers: { "content-type": "application/json", "PAYMENT-SIGNATURE": "proof" },
      body: JSON.stringify({ prompt: "hello" }),
    }),
  );
  assert.equal(response.status, 402);
  const encoded = response.headers.get("PAYMENT-RESPONSE");
  assert.ok(encoded);
  const settlement = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  assert.equal(settlement.success, false);
  assert.equal(settlement.transaction, transaction);
  assert.deepEqual(await response.json(), { error: "settlement reverted" });
});

test("internal quote uses string integers on the wire", async () => {
  const response = await new SellerHttpTransport(fakeApplication, "gemini").handle(
    new Request("http://seller.local/internal/quotes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        purchaseId: "purchase-1",
        requestId: "request-1",
        minInputLimit: 1,
        minOutputLimit: 1,
      }),
    }),
  );
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.quote.amount, "100000");
  assert.equal(payload.quote.expiresAt, "1800000120");
});

test("internal quote rejects unknown and malformed optional fields", async () => {
  const invalidBodies = [
    {
      purchaseId: "purchase-1", requestId: "request-1",
      minInputLimit: 1, minOutputLimit: 1, unknown: true,
    },
    {
      purchaseId: "purchase-1", requestId: "request-1",
      minInputLimit: 1, minOutputLimit: 1, maxLatencyMs: 0,
    },
    {
      purchaseId: "purchase-1", requestId: "request-1",
      minInputLimit: 1, minOutputLimit: 1, preferredModelId: "",
    },
    {
      purchaseId: "   ", requestId: "request-1",
      minInputLimit: 1, minOutputLimit: 1,
    },
    {
      purchaseId: "purchase-1", requestId: "   ",
      minInputLimit: 1, minOutputLimit: 1, preferredModelId: "   ",
    },
  ];
  for (const body of invalidBodies) {
    const response = await new SellerHttpTransport(fakeApplication, "gemini").handle(
      new Request("http://seller.local/internal/quotes", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
    assert.equal(response.status, 400);
  }
});

test("inference remains behind an x402 payment gate", async () => {
  const challenge = new Permit2ChallengeProvider(
    {
      async read() {
        return {
          modelId: "gemini-test",
          amount: 100_000n,
          token: "0x0000000000000000000000000000000000000002",
          payTo: "0x0000000000000000000000000000000000000003",
          expiresAt: BigInt(Math.floor(Date.now() / 1000) + 60),
        };
      },
    },
    "gemini",
  );
  const response = await new SellerHttpTransport(
    fakeApplication,
    "gemini",
    challenge,
  ).handle(
    new Request(
      "http://seller.local/v1/inference?purchaseId=purchase-1&quoteId=quote-1",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: "hello" }),
      },
    ),
  );
  assert.equal(response.status, 402);
  assert.deepEqual(await response.json(), { error: "payment required" });
  const encoded = response.headers.get("PAYMENT-REQUIRED");
  assert.ok(encoded);
  const required = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  assert.equal(required.x402Version, 2);
  assert.equal(required.accepts[0].network, "eip155:84532");
  assert.equal(required.accepts[0].amount, "100000");
  assert.equal(required.accepts[0].extra.assetTransferMethod, "permit2");
  assert.equal(required.accepts[0].extra.name, "PBL Agent Credit");
  assert.equal(required.accepts[0].extra.version, "1");
  assert.equal(required.extensions.eip2612GasSponsoring.info.version, "1");
});

test("facilitator verifies before delivery, settles after it, and returns PAYMENT-RESPONSE", async () => {
  const terms = {
    async read() {
      return {
        modelId: "gemini-test",
        amount: 100_000n,
        token: "0x0000000000000000000000000000000000000002" as const,
        payTo: "0x0000000000000000000000000000000000000003" as const,
        expiresAt: BigInt(Math.floor(Date.now() / 1000) + 60),
      };
    },
  };
  const accepted = {
    scheme: "exact",
    network: "eip155:84532",
    amount: "100000",
    asset: "0x0000000000000000000000000000000000000002",
    payTo: "0x0000000000000000000000000000000000000003",
    maxTimeoutSeconds: 30,
    extra: {
      assetTransferMethod: "permit2",
      name: "PBL Agent Credit",
      version: "1",
    },
  };
  const paymentSignature = Buffer.from(JSON.stringify({
    x402Version: 2,
    resource: { url: "http://seller.local/v1/inference" },
    accepted,
    payload: { signature: "0xpermit", permit2Authorization: {} },
    extensions: {},
  })).toString("base64");
  const calls: string[] = [];
  const transaction = `0x${"ab".repeat(32)}`;
  const gate = new FacilitatorPaymentGate({
    quotes: terms,
    facilitatorUrl: "https://facilitator.test",
    async fetchImpl(input, init) {
      const url = String(input);
      calls.push(url.endsWith("/verify") ? "verify" : "settle");
      const request = JSON.parse(String(init?.body));
      assert.equal(request.paymentRequirements.amount, "100000");
      if (url.endsWith("/verify")) {
        return new Response(JSON.stringify({ isValid: true, payer: "0xbuyer" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(JSON.stringify({
        success: true,
        transaction,
        network: "eip155:84532",
        payer: "0xbuyer",
      }), { status: 200, headers: { "content-type": "application/json" } });
    },
  });
  const application = {
    quote: fakeApplication.quote,
    async paidInference(args: {
      purchaseId: string; quoteId: string; prompt: string; paymentSignature?: string;
    }) {
      const authorization = await gate.authorize(args);
      calls.push(`provider:${authorization.modelId}`);
      const settlement = await gate.settle(authorization);
      return {
        result: {
          providerId: "gemini", modelId: authorization.modelId, modelVersion: "v1",
          responseId: "response-1", text: `answer:${args.prompt}`,
        },
        settlement,
      };
    },
  };
  const response = await new SellerHttpTransport(application, "gemini").handle(
    new Request("http://seller.local/v1/inference?purchaseId=purchase-1&quoteId=quote-1", {
      method: "POST",
      headers: { "content-type": "application/json", "PAYMENT-SIGNATURE": paymentSignature },
      body: JSON.stringify({ prompt: "hello" }),
    }),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["verify", "provider:gemini-test", "settle"]);
  const encoded = response.headers.get("PAYMENT-RESPONSE");
  assert.ok(encoded);
  const settlement = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  assert.equal(settlement.transaction, transaction);
  assert.equal((await response.json()).text, "answer:hello");
});
