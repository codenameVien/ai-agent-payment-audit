#!/usr/bin/env node

/**
 * Records a known, already-settled live Facilitator result without signing, broadcasting,
 * calling a Provider, or calling the Facilitator again. It exists solely for recovery of
 * an Evidence API write failure after a live settlement response was received.
 */

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const STATE_PATH = resolve(REPO_ROOT, ".aegis-live-runtime.env");

function requiredArg(name) {
  const index = process.argv.indexOf(name);
  const value = index >= 0 ? process.argv[index + 1] : undefined;
  if (!value || value.startsWith("--")) throw new Error(`${name} is required`);
  return value;
}

function parseEnv(text) {
  return Object.fromEntries(
    text.split(/\r?\n/).flatMap((line) => {
      const index = line.indexOf("=");
      return index > 0 ? [[line.slice(0, index), line.slice(index + 1)]] : [];
    }),
  );
}

const purchaseId = requiredArg("--purchase-id");
const transaction = requiredArg("--transaction");
const payer = requiredArg("--payer").toLowerCase();
const amount = requiredArg("--amount");
if (!/^0x[0-9a-fA-F]{64}$/.test(transaction)) {
  throw new Error("--transaction must be a 32-byte EVM transaction hash");
}
if (!/^0x[0-9a-f]{40}$/.test(payer)) throw new Error("--payer must be an EVM address");
if (!/^\d+$/.test(amount)) throw new Error("--amount must be integer token units");

const state = parseEnv(await readFile(STATE_PATH, "utf8"));
if (!state.AEGIS_API_URL || !state.INTERNAL_SERVICE_TOKEN) {
  throw new Error("live runtime state is unavailable; start npm run aegis:live-payment first");
}
const response = await fetch(`${state.AEGIS_API_URL}/internal/evidence/aegis/payments/settle`, {
  method: "POST",
  headers: {
    authorization: `Bearer ${state.INTERNAL_SERVICE_TOKEN}`,
    "content-type": "application/json",
  },
  body: JSON.stringify({
    purchase_id: purchaseId,
    facilitator_transaction: transaction,
    facilitator_network: "eip155:84532",
    facilitator_payer: payer,
    facilitator_amount: amount,
  }),
});
const body = await response.text();
if (!response.ok) throw new Error(`reconciliation record failed: HTTP ${response.status}: ${body}`);
process.stdout.write(`${JSON.stringify({ purchaseId, state: "SETTLED", transactionBroadcast: false })}\n`);
