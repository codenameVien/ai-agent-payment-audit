/**
 * Server-side guardrails for the opt-in live PBLC path.
 *
 * These tests never contact a Facilitator or a chain. They prove that a browser cannot
 * turn on settlement: the process must hold both an explicit approval flag and the key
 * that derives to the configured user-owned wallet.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { privateKeyToAccount } from "viem/accounts";

import {
  createPaymentExecutor,
  createProviderGatewayServer,
} from "../src/aegis/main.js";

const PRIVATE_KEY = `0x${"55".repeat(32)}` as `0x${string}`;
const ACCOUNT = privateKeyToAccount(PRIVATE_KEY);

function liveEnv(extra: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  return {
    AEGIS_EXECUTION_MODE: "live",
    AEGIS_REAL_PAYMENT_APPROVED: "yes",
    PBLC_USER_PRIVATE_KEY: PRIVATE_KEY,
    PBLC_USER_ADDRESS: ACCOUNT.address,
    INTERNAL_SERVICE_TOKEN: "internal-test-token",
    GATEWAY_SERVICE_TOKEN: "gateway-test-token",
    EVIDENCE_API_URL: "http://127.0.0.1:1",
    AEGIS_FACILITATOR_URL: "https://example.test/facilitator",
    AEGIS_GATEWAY_ROUTES_JSON: JSON.stringify({ openai: "http://127.0.0.1:2" }),
    AEGIS_PROVIDER_ID: "openai",
    ...extra,
  };
}

test("live executor requires the explicit approval gate and configured wallet key", () => {
  assert.throws(
    () => createPaymentExecutor(liveEnv({ AEGIS_REAL_PAYMENT_APPROVED: "" })),
    /AEGIS_REAL_PAYMENT_APPROVED=yes/,
  );
  assert.throws(
    () => createPaymentExecutor(liveEnv({ PBLC_USER_ADDRESS: "0x0000000000000000000000000000000000000001" })),
    /does not match PBLC_USER_ADDRESS/,
  );

  const executor = createPaymentExecutor(liveEnv());
  assert.equal(executor.buyerAddress.toLowerCase(), ACCOUNT.address.toLowerCase());
});

test("live Provider Gateway reports its server-side execution mode", async () => {
  const server = createProviderGatewayServer(liveEnv());
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const response = await fetch(`http://127.0.0.1:${address.port}/health`);
    assert.deepEqual(await response.json(), {
      status: "ok",
      providerId: "openai",
      providerModelId: "gpt-4.1-mini-2025-04-14",
      modelVersion: "2025-04-14",
      executionMode: "live",
    });
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    );
  }
});
