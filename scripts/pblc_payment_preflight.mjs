/**
 * Read-only preparation for a future PBLC V2 / x402 payment.
 *
 * This script deliberately does not create an authorization, call Facilitator /verify or
 * /settle, or broadcast a transaction. It needs only a public wallet address and proves
 * that it and the configured PBLC V2 contract line up before a separately approved payment.
 */

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { createPublicClient, formatUnits, http } from "viem";
import { baseSepolia } from "viem/chains";

const EXPECTED_TOKEN = "0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3";
const EXPECTED_NAME = "PBL Agent Credit";
const EXPECTED_SYMBOL = "PBLC";
const EXPECTED_DECIMALS = 6;

const ERC20_METADATA_ABI = [
  {
    type: "function",
    name: "name",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "string" }],
  },
  {
    type: "function",
    name: "symbol",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "string" }],
  },
  {
    type: "function",
    name: "decimals",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint8" }],
  },
  {
    type: "function",
    name: "balanceOf",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ type: "uint256" }],
  },
];

function envFromText(text) {
  const values = new Map();
  for (const line of text.split(/\r?\n/)) {
    if (!line || /^\s*#/.test(line)) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    values.set(line.slice(0, separator).trim(), line.slice(separator + 1).trim());
  }
  return values;
}

function required(values, key) {
  const value = values.get(key);
  if (!value) throw new Error(`${key} is required in .env.local`);
  return value;
}

function usage() {
  process.stdout.write([
    "PBLC V2 / x402 read-only payment preflight",
    "Reads .env.local and checks wallet, PBLC V2 metadata/balance, and Facilitator /supported.",
    "It does not sign, call /verify or /settle, or broadcast any transaction.",
  ].join("\n") + "\n");
}

if (process.argv.includes("--help")) {
  usage();
  process.exit(0);
}

const repoRoot = resolve(new URL("..", import.meta.url).pathname);
const values = envFromText(await readFile(resolve(repoRoot, ".env.local"), "utf8"));
const payer = required(values, "AEGIS_LIVE_PAYER_ADDRESS");
if (!/^0x[0-9a-fA-F]{40}$/.test(payer)) {
  throw new Error("AEGIS_LIVE_PAYER_ADDRESS is not an address");
}

const token = required(values, "PBLC_TOKEN_ADDRESS");
if (!/^0x[0-9a-fA-F]{40}$/.test(token)) throw new Error("PBLC_TOKEN_ADDRESS is not an address");
const rpcUrl = required(values, "BASE_SEPOLIA_RPC_URL");
const facilitatorUrl = (values.get("FACILITATOR_URL") || "https://x402.org/facilitator").replace(/\/$/, "");
const client = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });

const [nativeWei, name, symbol, decimals, tokenUnits, supportedResponse] = await Promise.all([
  client.getBalance({ address: payer }),
  client.readContract({ address: token, abi: ERC20_METADATA_ABI, functionName: "name" }),
  client.readContract({ address: token, abi: ERC20_METADATA_ABI, functionName: "symbol" }),
  client.readContract({ address: token, abi: ERC20_METADATA_ABI, functionName: "decimals" }),
  client.readContract({
    address: token,
    abi: ERC20_METADATA_ABI,
    functionName: "balanceOf",
    args: [payer],
  }),
  fetch(`${facilitatorUrl}/supported`).then(async (response) => {
    if (!response.ok) throw new Error(`Facilitator /supported returned HTTP ${response.status}`);
    return response.json();
  }),
]);

const supportsExactBaseSepolia = Array.isArray(supportedResponse?.kinds) && supportedResponse.kinds.some(
  (kind) => kind?.x402Version === 2 && kind?.scheme === "exact" && kind?.network === "eip155:84532",
);
const tokenMatches = token.toLowerCase() === EXPECTED_TOKEN.toLowerCase();
const metadataMatches = name === EXPECTED_NAME && symbol === EXPECTED_SYMBOL && Number(decimals) === EXPECTED_DECIMALS;

process.stdout.write(`${JSON.stringify({
  mode: "read-only-preflight",
  transactionBroadcast: false,
  authorizationSignatureCreated: false,
  facilitatorVerifyCalled: false,
  facilitatorSettleCalled: false,
  network: "eip155:84532",
  payer,
  nativeBalanceWei: nativeWei.toString(),
  pblc: {
    address: token,
    expectedAddress: EXPECTED_TOKEN,
    addressMatchesExpected: tokenMatches,
    name,
    symbol,
    decimals: Number(decimals),
    metadataMatchesExpected: metadataMatches,
    balanceUnits: tokenUnits.toString(),
    balance: formatUnits(tokenUnits, Number(decimals)),
  },
  facilitator: {
    url: facilitatorUrl,
    supportsV2ExactBaseSepolia: supportsExactBaseSepolia,
    note: "This endpoint does not prove PBLC custom-token acceptance; that requires a separately approved verify/settle smoke.",
  },
}, null, 2)}\n`);

if (!tokenMatches || !metadataMatches || !supportsExactBaseSepolia) process.exitCode = 2;
