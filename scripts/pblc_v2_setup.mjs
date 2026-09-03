#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createPublicClient,
  createWalletClient,
  encodeDeployData,
  formatEther,
  getContractAddress,
  http,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ENV_PATH = resolve(REPO_ROOT, ".env.local");
const ARTIFACT_PATH = resolve(
  REPO_ROOT,
  "infra/contracts/out/DemoTokenV2.sol/DemoTokenV2.json",
);
const INITIAL_SUPPLY_UNITS = 1_000_000n * 1_000_000n;

function parseEnv(text) {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    if (!line || line.trimStart().startsWith("#") || !line.includes("=")) continue;
    const separator = line.indexOf("=");
    values[line.slice(0, separator)] = line.slice(separator + 1);
  }
  return values;
}

function required(values, name) {
  const value = values[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function context() {
  const env = parseEnv(await readFile(ENV_PATH, "utf8"));
  const artifact = JSON.parse(await readFile(ARTIFACT_PATH, "utf8"));
  const deployer = privateKeyToAccount(required(env, "DEPLOYER_PRIVATE_KEY"));
  const buyer = privateKeyToAccount(required(env, "BUYER_AGENT_PRIVATE_KEY"));
  const rpcUrl = env.BASE_SEPOLIA_RPC_URL?.trim() || "https://sepolia.base.org";
  const publicClient = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });
  const walletClient = createWalletClient({ account: deployer, chain: baseSepolia, transport: http(rpcUrl) });
  const args = [deployer.address, buyer.address, INITIAL_SUPPLY_UNITS];
  return { env, artifact, deployer, buyer, publicClient, walletClient, args };
}

async function plan() {
  const value = await context();
  const [nonce, fees, balance] = await Promise.all([
    value.publicClient.getTransactionCount({ address: value.deployer.address, blockTag: "pending" }),
    value.publicClient.estimateFeesPerGas(),
    value.publicClient.getBalance({ address: value.deployer.address }),
  ]);
  const data = encodeDeployData({
    abi: value.artifact.abi,
    bytecode: value.artifact.bytecode.object,
    args: value.args,
  });
  const gas = await value.publicClient.estimateGas({ account: value.deployer.address, data });
  const maxFeePerGas = fees.maxFeePerGas ?? fees.gasPrice;
  const estimatedMaxFee = maxFeePerGas === undefined ? null : gas * maxFeePerGas;
  process.stdout.write(`${JSON.stringify({
    action: "PLAN_ONLY_NO_TRANSACTION_SENT",
    network: "Base Sepolia",
    chainId: baseSepolia.id,
    predictedTokenAddress: getContractAddress({ from: value.deployer.address, nonce: BigInt(nonce) }),
    deployerAddress: value.deployer.address,
    deployerEth: formatEther(balance),
    initialHolder: value.buyer.address,
    initialSupplyUnits: INITIAL_SUPPLY_UNITS.toString(),
    tokenName: "PBL Agent Credit",
    tokenSymbol: "PBLC",
    tokenVersion: "2",
    decimals: 6,
    smokePaymentUnits: "100000",
    smokePaymentDisplay: "0.1 PBLC",
    estimatedGas: gas.toString(),
    estimatedMaxFeeWei: estimatedMaxFee?.toString() ?? null,
    estimatedMaxFeeEth: estimatedMaxFee === null ? null : formatEther(estimatedMaxFee),
  }, null, 2)}\n`);
}

async function deploy() {
  const value = await context();
  if (value.env.PBLC_V2_DEPLOY_APPROVED !== "yes") {
    throw new Error("deployment blocked: set PBLC_V2_DEPLOY_APPROVED=yes only after explicit approval");
  }
  const hash = await value.walletClient.deployContract({
    abi: value.artifact.abi,
    bytecode: value.artifact.bytecode.object,
    args: value.args,
  });
  const receipt = await value.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success" || !receipt.contractAddress) {
    throw new Error(`PBLC V2 deployment failed: ${hash}`);
  }
  process.stdout.write(`${JSON.stringify({
    transactionHash: hash,
    contractAddress: receipt.contractAddress,
    blockNumber: Number(receipt.blockNumber),
  }, null, 2)}\n`);
}

const command = process.argv[2] ?? "plan";
if (command === "plan") await plan();
else if (command === "deploy") await deploy();
else throw new Error(`unknown command: ${command}`);
