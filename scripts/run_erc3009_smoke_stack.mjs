#!/usr/bin/env node

import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createCommerceGatewayServer } from "../services/commerce-gateway/dist/src/main.js";
import { createSellerServer } from "../services/seller-service/dist/src/main.js";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TOKEN_ADDRESS = process.argv[2];
if (!/^0x[0-9a-fA-F]{40}$/.test(TOKEN_ADDRESS ?? "")) {
  throw new Error("usage: node scripts/run_erc3009_smoke_stack.mjs <PBLC_V2_ADDRESS>");
}

function parseEnv(text) {
  const values = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const separator = line.indexOf("=");
    const name = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (value.length >= 2 && value[0] === value.at(-1) && ["\"", "'"].includes(value[0])) {
      value = value.slice(1, -1);
    }
    values[name] = value;
  }
  return values;
}

function required(env, name) {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const local = parseEnv(await readFile(resolve(REPO_ROOT, ".env.local"), "utf8"));
const shared = { ...process.env, ...local };
const apiUrl = "http://127.0.0.1:8100";
const modelDefaults = {
  gemini: "gemini-2.5-flash",
  nemotron: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
};

const sellerEnv = (provider) => {
  const upper = provider === "gemini" ? "GEMINI" : "NEMOTRON";
  const port = provider === "gemini" ? "8180" : "8182";
  const apiKey = provider === "gemini" ? shared.GEMINI_API_KEY : shared.NVIDIA_API_KEY;
  return {
    ...shared,
    PORT: port,
    PROVIDER_ID: provider,
    PROVIDER_MODE: "mock",
    PROVIDER_API_KEY: apiKey ?? "",
    SELLER_PRIVATE_KEY: required(shared, `${upper}_SELLER_PRIVATE_KEY`),
    SELLER_AGENT_ID: `seller-${provider}`,
    ERC8004_AGENT_ID: required(shared, `${upper}_ERC8004_AGENT_ID`),
    MODEL_ID: shared[`${upper}_MODEL_ID`] || modelDefaults[provider],
    MODEL_VERSION:
      shared[`${upper}_MODEL_VERSION`] || shared[`${upper}_MODEL_ID`] || modelDefaults[provider],
    MODEL_PRICE_UNITS: shared[`${upper}_MODEL_PRICE_UNITS`] || "100000",
    TOKEN_ADDRESS,
    PAY_TO_ADDRESS: required(shared, `${upper}_PAY_TO_ADDRESS`),
    EVIDENCE_API_URL: apiUrl,
  };
};

const apiEnv = {
  ...shared,
  MONGODB_URI: "mongodb://127.0.0.1:27018/?replicaSet=pblrs&directConnection=true",
  MONGODB_DATABASE: "pbl_audit",
  COMMERCE_GATEWAY_URL: "http://127.0.0.1:8181",
  SELLER_ROUTES_JSON: JSON.stringify({
    gemini: "http://127.0.0.1:8180",
    nemotron: "http://127.0.0.1:8182",
  }),
};
const gatewayEnv = {
  ...shared,
  PORT: "8181",
  EVIDENCE_API_URL: apiUrl,
  BUYER_PRIVATE_KEY: required(shared, "BUYER_AGENT_PRIVATE_KEY"),
  SELLER_ROUTES_JSON: JSON.stringify({
    "seller-gemini": "http://127.0.0.1:8180/v1/inference",
    "seller-nemotron": "http://127.0.0.1:8182/v1/inference",
  }),
};

const gemini = createSellerServer(sellerEnv("gemini"));
const nemotron = createSellerServer(sellerEnv("nemotron"));
const gateway = createCommerceGatewayServer(gatewayEnv);
gemini.listen(8180, "127.0.0.1");
nemotron.listen(8182, "127.0.0.1");
gateway.listen(8181, "127.0.0.1");

const api = spawn(
  "uv",
  [
    "run",
    "--project",
    "services/buyer-audit-api",
    "uvicorn",
    "buyer_audit_api.main:app",
    "--app-dir",
    "services/buyer-audit-api/src",
    "--host",
    "127.0.0.1",
    "--port",
    "8100",
  ],
  { cwd: REPO_ROOT, env: apiEnv, stdio: "inherit" },
);

process.stdout.write(
  `${JSON.stringify({
    mode: "isolated-erc3009-smoke",
    token: TOKEN_ADDRESS,
    api: apiUrl,
    paymentExecutor: "http://127.0.0.1:8181",
    sellers: ["http://127.0.0.1:8180", "http://127.0.0.1:8182"],
  }, null, 2)}\n`,
);

let stopping = false;
function stop(signal) {
  if (stopping) return;
  stopping = true;
  api.kill(signal);
  gemini.close();
  nemotron.close();
  gateway.close();
  setTimeout(() => process.exit(0), 250).unref();
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));
api.on("exit", (code) => {
  if (!stopping) {
    process.stderr.write(`isolated API exited with code ${code ?? "unknown"}\n`);
    stop("SIGTERM");
  }
});
