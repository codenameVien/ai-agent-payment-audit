import assert from "node:assert/strict";
import test from "node:test";

import { createCommerceGatewayServer } from "../src/main.js";

const ADDRESS = "0x0000000000000000000000000000000000000001";
const PRIVATE_KEY = `0x${"22".repeat(32)}`;

test("runtime assembly wires the operational Gateway and protects execution endpoints", async () => {
  const server = createCommerceGatewayServer({
    BASE_SEPOLIA_RPC_URL: "http://127.0.0.1:1",
    BUYER_PRIVATE_KEY: PRIVATE_KEY,
    EVIDENCE_API_URL: "http://127.0.0.1:2",
    INTERNAL_SERVICE_TOKEN: "internal-token",
    GATEWAY_SERVICE_TOKEN: "gateway-token",
    DECISION_VERIFYING_CONTRACT: ADDRESS,
    EVIDENCE_ANCHOR_ADDRESS: ADDRESS,
    SELLER_ROUTES_JSON: JSON.stringify({
      "seller-gemini": "http://127.0.0.1:3/v1/inference",
      "seller-nemotron": "http://127.0.0.1:4/v1/inference",
    }),
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const origin = `http://127.0.0.1:${address.port}`;
    assert.deepEqual(await (await fetch(`${origin}/health`)).json(), { status: "ok" });
    assert.equal((await fetch(`${origin}/execute`, { method: "POST" })).status, 401);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    );
  }
});

test("the reputation writer is disabled unless an operator turns it on", async () => {
  const server = createCommerceGatewayServer({
    BASE_SEPOLIA_RPC_URL: "http://127.0.0.1:1",
    BUYER_PRIVATE_KEY: PRIVATE_KEY,
    EVIDENCE_API_URL: "http://127.0.0.1:2",
    INTERNAL_SERVICE_TOKEN: "internal-token",
    GATEWAY_SERVICE_TOKEN: "gateway-token",
    DECISION_VERIFYING_CONTRACT: ADDRESS,
    SELLER_ROUTES_JSON: JSON.stringify({
      "seller-gemini": "http://127.0.0.1:3/v1/inference",
    }),
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const origin = `http://127.0.0.1:${address.port}`;
    assert.equal(
      (await fetch(`${origin}/reputation-query`, { method: "POST" })).status,
      401,
    );
    assert.equal((await fetch(`${origin}/reputation`, { method: "POST" })).status, 401);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    );
  }
});

test("a production root never accepts a faked ERC-8004 write mode", () => {
  const env = {
    BASE_SEPOLIA_RPC_URL: "http://127.0.0.1:1",
    BUYER_PRIVATE_KEY: PRIVATE_KEY,
    EVIDENCE_API_URL: "http://127.0.0.1:2",
    INTERNAL_SERVICE_TOKEN: "internal-token",
    GATEWAY_SERVICE_TOKEN: "gateway-token",
    DECISION_VERIFYING_CONTRACT: ADDRESS,
    SELLER_ROUTES_JSON: JSON.stringify({
      "seller-gemini": "http://127.0.0.1:3/v1/inference",
    }),
  };
  assert.throws(
    () => createCommerceGatewayServer({ ...env, ERC8004_WRITE_MODE: "fake" }),
    /never fake a chain/,
  );
  assert.throws(
    () => createCommerceGatewayServer({ ...env, ERC8004_WRITE_MODE: "simulate" }),
    /must be disabled or live/,
  );
  assert.ok(createCommerceGatewayServer({ ...env, ERC8004_WRITE_MODE: "live" }));
});
