import assert from "node:assert/strict";
import test from "node:test";

import { ExactEvmChallengeProvider } from "../src/x402.js";

test("seller advertises x402 v2 exact eip3009 without Permit2 sponsorship", async () => {
  const challenge = await new ExactEvmChallengeProvider(
    { async read() { return {
      modelId: "gemini-test",
      amount: 100000n,
      token: "0x0000000000000000000000000000000000000003",
      payTo: "0x0000000000000000000000000000000000000004",
      expiresAt: BigInt(Math.floor(Date.now() / 1000) + 300),
    }; } },
    "gemini",
    { transferMethod: "eip3009", tokenVersion: "2" },
  ).challenge("purchase-1", "quote-1", "https://seller.test/inference");
  assert.equal(challenge.x402Version, 2);
  assert.equal(challenge.accepts[0]?.scheme, "exact");
  assert.equal(challenge.accepts[0]?.extra.assetTransferMethod, "eip3009");
  assert.equal(challenge.accepts[0]?.extra.version, "2");
  assert.deepEqual(challenge.extensions, {});
});
