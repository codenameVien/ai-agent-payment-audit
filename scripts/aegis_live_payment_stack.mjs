#!/usr/bin/env node

/**
 * Local runtime for a real Base Sepolia PBLC payment with Mock Provider output.
 *
 * It deliberately does not create a purchase, sign an authorization, call a Facilitator,
 * or broadcast a transaction at startup.  Those actions occur only after the user checks
 * the consent box and submits one request in `/request`.  MongoDB is pre-existing and is
 * never created, reset, or removed by this runner.
 */

import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PROVIDERS = ["openai", "anthropic", "google"];

function parseEnv(text) {
  const result = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const name = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (value.length >= 2 && value[0] === value.at(-1) && ["'", '"'].includes(value[0])) {
      value = value.slice(1, -1);
    }
    result[name] = value;
  }
  return result;
}

function required(env, name) {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required in .env.local`);
  return value;
}

/**
 * These authenticate only loopback processes for this one runner lifetime. They are
 * unrelated to the user wallet and deliberately stay out of .env.local and stdout.
 */
function ensureEphemeralServiceToken(env, name) {
  if (env[name]?.trim()) return;
  env[name] = randomBytes(32).toString("base64url");
}

function port(env, name, fallback) {
  const value = Number(env[name] || fallback);
  if (!Number.isSafeInteger(value) || value < 1024 || value > 65535) {
    throw new Error(`${name} must be a local TCP port`);
  }
  return value;
}

function loopbackUrl(value, label) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)) {
    throw new Error(`${label} must be an http loopback URL`);
  }
  return url;
}

function httpsUrl(value, label) {
  const url = new URL(value);
  if (url.protocol !== "https:") throw new Error(`${label} must use https in live mode`);
  return url;
}

function start(name, command, args, env) {
  const child = spawn(command, args, { cwd: REPO_ROOT, env, stdio: "inherit" });
  child.on("exit", (code, signal) => {
    if (!stopping && code !== 0) {
      process.stderr.write(`[${name}] exited: code=${code ?? "unknown"} signal=${signal ?? "none"}\n`);
    }
  });
  children.push(child);
  return child;
}

async function waitFor(url, name) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) return response;
    } catch {
      // Child process is still starting.
    }
    await new Promise((done) => setTimeout(done, 200));
  }
  throw new Error(`${name} did not become healthy: ${url}`);
}

const local = parseEnv(await readFile(resolve(REPO_ROOT, ".env.local"), "utf8"));
const env = { ...process.env, ...local };
if (env.AEGIS_EXECUTION_MODE !== "live") {
  throw new Error("Refusing to start: set AEGIS_EXECUTION_MODE=live in .env.local");
}
if (env.AEGIS_REAL_PAYMENT_APPROVED !== "yes") {
  throw new Error("Refusing to start: set AEGIS_REAL_PAYMENT_APPROVED=yes in .env.local");
}
required(env, "PBLC_USER_ADDRESS");
required(env, "PBLC_USER_PRIVATE_KEY");
required(env, "MONGODB_URI");
ensureEphemeralServiceToken(env, "INTERNAL_SERVICE_TOKEN");
ensureEphemeralServiceToken(env, "ADMIN_SERVICE_TOKEN");
ensureEphemeralServiceToken(env, "GATEWAY_SERVICE_TOKEN");
const facilitatorUrl = httpsUrl(required(env, "AEGIS_FACILITATOR_URL"), "AEGIS_FACILITATOR_URL");

const apiPort = port(env, "AEGIS_API_PORT", "8100");
const executorPort = port(env, "AEGIS_EXECUTOR_PORT", "8094");
const gatewayPorts = {
  openai: port(env, "AEGIS_OPENAI_GATEWAY_PORT", "8091"),
  anthropic: port(env, "AEGIS_ANTHROPIC_GATEWAY_PORT", "8092"),
  google: port(env, "AEGIS_GOOGLE_GATEWAY_PORT", "8093"),
};
const apiUrl = `http://127.0.0.1:${apiPort}`;
const executorUrl = `http://127.0.0.1:${executorPort}`;
const routes = Object.fromEntries(
  PROVIDERS.map((provider) => [provider, `http://127.0.0.1:${gatewayPorts[provider]}`]),
);
for (const [provider, route] of Object.entries(routes)) loopbackUrl(route, `${provider} route`);

const children = [];
let stopping = false;
let releaseStop;
function stop(signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  for (const child of children.reverse()) {
    if (child.exitCode === null && child.signalCode === null) child.kill(signal);
  }
  releaseStop?.();
}

try {
  // The API owns MongoDB; these three children have only its internal evidence interface.
  start("evidence-api", "uv", [
    "run", "--project", "services/buyer-audit-api", "uvicorn", "buyer_audit_api.main:app",
    "--app-dir", "services/buyer-audit-api/src", "--host", "127.0.0.1", "--port", String(apiPort),
  ], {
    ...env,
    COMMERCE_GATEWAY_URL: executorUrl,
    AEGIS_NETWORK: "eip155:84532",
  });

  for (const provider of PROVIDERS) {
    start(`gateway-${provider}`, process.execPath, [
      "services/commerce-gateway/dist/src/aegis/main.js", "provider-gateway",
    ], {
      ...env,
      PORT: String(gatewayPorts[provider]),
      AEGIS_PROVIDER_ID: provider,
      AEGIS_NETWORK: "eip155:84532",
      AEGIS_GATEWAY_ROUTES_JSON: JSON.stringify(routes),
      EVIDENCE_API_URL: apiUrl,
      AEGIS_FACILITATOR_URL: facilitatorUrl.toString().replace(/\/$/, ""),
    });
  }

  start("payment-executor", process.execPath, [
    "services/commerce-gateway/dist/src/aegis/main.js", "payment-executor",
  ], {
    ...env,
    PORT: String(executorPort),
    AEGIS_NETWORK: "eip155:84532",
    AEGIS_GATEWAY_ROUTES_JSON: JSON.stringify(routes),
    EVIDENCE_API_URL: apiUrl,
  });

  await waitFor(`${apiUrl}/health`, "Evidence API");
  const executorHealth = await waitFor(`${executorUrl}/health`, "payment executor");
  const health = await executorHealth.json();
  if (health.executionMode !== "live") throw new Error("payment executor did not enter live mode");
  for (const provider of PROVIDERS) await waitFor(`${routes[provider]}/health`, `${provider} gateway`);

  const addressResponse = await fetch(`${executorUrl}/address`, {
    headers: { authorization: `Bearer ${env.GATEWAY_SERVICE_TOKEN}` },
  });
  if (!addressResponse.ok) throw new Error("payment executor did not disclose its configured payer address");
  const { buyerWalletAddress } = await addressResponse.json();
  if (String(buyerWalletAddress).toLowerCase() !== env.PBLC_USER_ADDRESS.toLowerCase()) {
    throw new Error("payment executor payer does not match PBLC_USER_ADDRESS");
  }

  const headers = {
    "content-type": "application/json",
    authorization: `Bearer ${env.INTERNAL_SERVICE_TOKEN}`,
  };
  const bind = await fetch(`${apiUrl}/internal/auth/buyer-wallet`, {
    method: "PUT",
    headers: { ...headers, authorization: `Bearer ${env.ADMIN_SERVICE_TOKEN}` },
    body: JSON.stringify({ owner_address: env.AEGIS_LOCAL_OWNER_ADDRESS, buyer_wallet_address: buyerWalletAddress }),
  });
  if (!bind.ok) throw new Error(`binding live payer failed: HTTP ${bind.status}`);
  const policy = await fetch(`${apiUrl}/internal/evidence/wallet-policies`, {
    method: "PUT",
    headers,
    body: JSON.stringify({
      buyer_wallet_address: buyerWalletAddress,
      policy_date: new Date().toISOString().slice(0, 10),
      token: env.PBLC_TOKEN_ADDRESS,
      per_transaction_limit_units: Number(env.AEGIS_PER_TRANSACTION_LIMIT_UNITS || "100000"),
      daily_limit_units: Number(env.AEGIS_DAILY_LIMIT_UNITS || "1000000"),
    }),
  });
  if (!policy.ok) {
    const detail = await policy.text();
    if (!detail.includes("active wallet policy cannot be replaced")) {
      throw new Error(`configuring live wallet policy failed: HTTP ${policy.status}`);
    }
  }

  process.stdout.write(`${JSON.stringify({
    mode: "live-pblc-payment-mock-provider",
    transactionBroadcastAtStartup: false,
    api: apiUrl,
    request: "Start the dashboard with API_ORIGIN set to this API, then open /request.",
    executor: executorUrl,
    facilitator: facilitatorUrl.toString(),
    payer: buyerWalletAddress,
    token: env.PBLC_TOKEN_ADDRESS,
    providerOutput: "mock",
    note: "A PBLC transfer can occur only after a user submits one consented request.",
  }, null, 2)}\n`);
} catch (error) {
  stop();
  throw error;
}

await new Promise((resolveStop) => {
  releaseStop = resolveStop;
  process.on("SIGINT", () => stop("SIGINT"));
  process.on("SIGTERM", () => stop("SIGTERM"));
});
