#!/usr/bin/env node

/**
 * Deploy the separate user-owned PBLC ERC-3009 contract.
 *
 * `plan` is public-address-only. `deploy` uses PBLC_USER_PRIVATE_KEY solely in this
 * local process and refuses a key whose derived address differs from PBLC_USER_ADDRESS.
 * It mints the initial supply to the same user wallet in the constructor.
 */

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
  isAddress,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ENV_PATH = resolve(ROOT, ".env.local");
const ARTIFACT_PATH = resolve(ROOT, "infra/contracts/out/DemoTokenV2.sol/DemoTokenV2.json");
const INITIAL_SUPPLY_UNITS = 1_000_000n * 1_000_000n;

function parseEnv(text) {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    if (!line || line.trimStart().startsWith("#") || !line.includes("=")) continue;
    const divider = line.indexOf("=");
    values[line.slice(0, divider)] = line.slice(divider + 1).trim();
  }
  return values;
}

function required(values, name) {
  const value = values[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function publicContext() {
  const env = parseEnv(await readFile(ENV_PATH, "utf8"));
  const userAddress = required(env, "PBLC_USER_ADDRESS");
  if (!isAddress(userAddress)) throw new Error("PBLC_USER_ADDRESS must be an address");
  const artifact = JSON.parse(await readFile(ARTIFACT_PATH, "utf8"));
  const rpcUrl = env.BASE_SEPOLIA_RPC_URL || "https://sepolia.base.org";
  const publicClient = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });
  return { env, userAddress, artifact, publicClient, rpcUrl };
}

async function plan() {
  const value = await publicContext();
  const [nonce, balance, fees] = await Promise.all([
    value.publicClient.getTransactionCount({ address: value.userAddress, blockTag: "pending" }),
    value.publicClient.getBalance({ address: value.userAddress }),
    value.publicClient.estimateFeesPerGas(),
  ]);
  const data = encodeDeployData({
    abi: value.artifact.abi,
    bytecode: value.artifact.bytecode.object,
    args: [value.userAddress, value.userAddress, INITIAL_SUPPLY_UNITS],
  });
  const gas = await value.publicClient.estimateGas({ account: value.userAddress, data });
  const maxFeePerGas = fees.maxFeePerGas ?? fees.gasPrice;
  const predictedAddress = getContractAddress({ from: value.userAddress, nonce: BigInt(nonce) });
  const code = await value.publicClient.getCode({ address: predictedAddress });
  process.stdout.write(`${JSON.stringify({
    action: "PLAN_ONLY_NO_TRANSACTION_SENT",
    network: "Base Sepolia",
    chainId: baseSepolia.id,
    deployerOwnerInitialHolder: value.userAddress,
    pendingNonce: nonce,
    predictedTokenAddress: predictedAddress,
    predictedAddressHasCode: code !== undefined && code !== "0x",
    nativeBalanceEth: formatEther(balance),
    initialSupplyUnits: INITIAL_SUPPLY_UNITS.toString(),
    initialSupplyDisplay: "1000000 PBLC",
    estimatedGas: gas.toString(),
    maxFeePerGasWei: maxFeePerGas?.toString() ?? null,
    estimatedMaxFeeEth: maxFeePerGas === undefined ? null : formatEther(gas * maxFeePerGas),
  }, null, 2)}\n`);
}

async function deploy() {
  const value = await publicContext();
  if (process.env.PBLC_USER_TOKEN_DEPLOY_APPROVED !== "yes") {
    throw new Error("deployment blocked: PBLC_USER_TOKEN_DEPLOY_APPROVED=yes is required");
  }
  const privateKey = required(value.env, "PBLC_USER_PRIVATE_KEY");
  const account = privateKeyToAccount(privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`);
  if (account.address.toLowerCase() !== value.userAddress.toLowerCase()) {
    throw new Error("PBLC_USER_PRIVATE_KEY does not match PBLC_USER_ADDRESS");
  }
  const walletClient = createWalletClient({ account, chain: baseSepolia, transport: http(value.rpcUrl) });
  const hash = await walletClient.deployContract({
    abi: value.artifact.abi,
    bytecode: value.artifact.bytecode.object,
    args: [account.address, account.address, INITIAL_SUPPLY_UNITS],
  });
  const receipt = await value.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success" || !receipt.contractAddress) {
    throw new Error(`PBLC deployment failed: ${hash}`);
  }
  process.stdout.write(`${JSON.stringify({
    action: "DEPLOYED_AND_INITIAL_SUPPLY_MINTED",
    transactionHash: hash,
    contractAddress: receipt.contractAddress,
    deployerOwnerInitialHolder: account.address,
    initialSupplyUnits: INITIAL_SUPPLY_UNITS.toString(),
    initialSupplyDisplay: "1000000 PBLC",
    blockNumber: receipt.blockNumber.toString(),
  }, null, 2)}\n`);
}

const command = process.argv[2] ?? "plan";
if (command === "plan") await plan();
else if (command === "deploy") await deploy();
else throw new Error(`unknown command: ${command}`);
