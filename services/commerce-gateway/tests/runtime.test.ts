import assert from "node:assert/strict";
import { createServer } from "node:http";
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

/** An in-process JSON-RPC stand-in. No chain is contacted, and none is simulated. */
function fakeRpc(head: bigint) {
  return createServer((request, response) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const call: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      assert.ok(call !== null && typeof call === "object" && "id" in call && "method" in call);
      const result =
        call.method === "eth_blockNumber" ? `0x${head.toString(16)}`
        : call.method === "eth_chainId" ? "0x14a34"
        : call.method === "eth_getLogs" ? []
        : null;
      response.statusCode = 200;
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ jsonrpc: "2.0", id: call.id, result }));
    })();
  });
}

test("the reputation read endpoint only accepts a bounded window from the head", async () => {
  const rpc = fakeRpc(4096n);
  await new Promise<void>((resolve) => rpc.listen(0, "127.0.0.1", resolve));
  const rpcAddress = rpc.address();
  assert.ok(rpcAddress && typeof rpcAddress === "object");
  const server = createCommerceGatewayServer({
    BASE_SEPOLIA_RPC_URL: `http://127.0.0.1:${rpcAddress.port}`,
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
    const base = { erc8004AgentId: "1", trustedClients: [ADDRESS] };
    const query = (body: unknown, headers: Record<string, string> = {}) =>
      fetch(`${origin}/reputation-query`, {
        method: "POST",
        headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify(body),
      });
    const authorized = { authorization: "Bearer gateway-token" };

    const unauthenticated = await query({ ...base, lookbackBlocks: 100 });
    assert.equal(unauthenticated.status, 401);

    const missing = await query(base, authorized);
    assert.equal(missing.status, 409);
    const missingBody: unknown = await missing.json();
    assert.ok(missingBody !== null && typeof missingBody === "object" && "error" in missingBody);
    assert.match(String(missingBody.error), /lookbackBlocks is required/);

    const unbounded = await query({ ...base, lookbackBlocks: 100_001 }, authorized);
    assert.equal(unbounded.status, 409);
    const unboundedBody: unknown = await unbounded.json();
    assert.ok(
      unboundedBody !== null && typeof unboundedBody === "object" && "error" in unboundedBody,
    );
    assert.match(String(unboundedBody.error), /lookbackBlocks must be an integer in 1\.\.100000/);

    const bounded = await query({ ...base, lookbackBlocks: 100 }, authorized);
    assert.equal(bounded.status, 200);
    const payload: unknown = await bounded.json();
    assert.ok(payload !== null && typeof payload === "object");
    assert.ok(
      "latestBlock" in payload &&
        "fromBlock" in payload &&
        "toBlock" in payload &&
        "erc8004AgentId" in payload &&
        "trustedClients" in payload &&
        "tag1" in payload &&
        "tag2" in payload &&
        "events" in payload,
    );
    // The buyer only trusts an echo it can recompute: head, window and scope must agree.
    assert.equal(payload.latestBlock, 4096);
    assert.equal(payload.toBlock, 4096);
    assert.equal(payload.fromBlock, 3997);
    assert.equal(payload.erc8004AgentId, "1");
    assert.deepEqual(payload.trustedClients, [ADDRESS.toLowerCase()]);
    assert.equal(payload.tag1, "pbl-audit");
    assert.equal(payload.tag2, "payment-outcome");
    assert.deepEqual(payload.events, []);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => error ? reject(error) : resolve()),
    );
    await new Promise<void>((resolve, reject) =>
      rpc.close((error) => error ? reject(error) : resolve()),
    );
  }
});
