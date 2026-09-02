#!/usr/bin/env node

import { chmod, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import {
  createPublicClient,
  createWalletClient,
  decodeEventLog,
  getAddress,
  http,
  parseAbi,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ENV_PATH = resolve(REPO_ROOT, ".env.local");
const DEFAULT_RPC_URL = "https://sepolia.base.org";
const DEFAULT_IDENTITY_REGISTRY = "0x8004A818BFB912233c491871b3d84c89A494BD9e";
const DEFAULT_REPUTATION_REGISTRY = "0x8004B663056A597Dffe9eCcC1965A193B7388713";

const identityAbi = parseAbi([
  "event Registered(uint256 indexed agentId, string agentURI, address indexed owner)",
  "function register(string agentURI) returns (uint256 agentId)",
  "function ownerOf(uint256 agentId) view returns (address)",
  "function getAgentWallet(uint256 agentId) view returns (address)",
  "function setAgentWallet(uint256 agentId, address newWallet, uint256 deadline, bytes signature)",
  "function getVersion() view returns (string)",
]);

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
  if (!value) throw new Error(`${name} is required; run scripts/setup_keys.py first`);
  return value;
}

function replaceEnvValue(text, name, value) {
  const lines = text.split(/\r?\n/);
  const index = lines.findIndex((line) => line.startsWith(`${name}=`));
  if (index === -1) lines.push(`${name}=${value}`);
  else lines[index] = `${name}=${value}`;
  return `${lines.join("\n").trimEnd()}\n`;
}

async function persistEnvValue(name, value) {
  const current = await readFile(ENV_PATH, "utf8");
  await writeFile(ENV_PATH, replaceEnvValue(current, name, value), {
    encoding: "utf8",
    mode: 0o600,
  });
  await chmod(ENV_PATH, 0o600);
}

async function runtime() {
  const envText = await readFile(ENV_PATH, "utf8");
  const env = parseEnv(envText);
  const rpcUrl = env.BASE_SEPOLIA_RPC_URL?.trim() || DEFAULT_RPC_URL;
  const identityRegistry = getAddress(
    env.ERC8004_IDENTITY_REGISTRY?.trim() || DEFAULT_IDENTITY_REGISTRY,
  );
  const reputationRegistry = getAddress(
    env.ERC8004_REPUTATION_REGISTRY?.trim() || DEFAULT_REPUTATION_REGISTRY,
  );
  const deployer = privateKeyToAccount(required(env, "DEPLOYER_PRIVATE_KEY"));
  const geminiSeller = privateKeyToAccount(required(env, "GEMINI_SELLER_PRIVATE_KEY"));
  const nemotronSeller = privateKeyToAccount(required(env, "NEMOTRON_SELLER_PRIVATE_KEY"));
  const publicClient = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });
  const walletClient = createWalletClient({
    account: deployer,
    chain: baseSepolia,
    transport: http(rpcUrl),
  });
  return {
    env,
    rpcUrl,
    identityRegistry,
    reputationRegistry,
    deployer,
    geminiSeller,
    nemotronSeller,
    publicClient,
    walletClient,
  };
}

async function requireSuccessfulReceipt(publicClient, hash, label) {
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`${label} transaction reverted: ${hash}`);
  return receipt;
}

async function readContractAtConfirmedBlock(publicClient, request, blockNumber) {
  if (blockNumber === null) return publicClient.readContract(request);
  let lastError;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      return await publicClient.readContract({ ...request, blockNumber });
    } catch (error) {
      lastError = error;
      if (!String(error).includes("block not found")) throw error;
      await delay(2_000);
    }
  }
  throw lastError;
}

function registeredAgentId(receipt, registry, expectedOwner) {
  for (const log of receipt.logs) {
    if (log.address.toLowerCase() !== registry.toLowerCase()) continue;
    try {
      const decoded = decodeEventLog({
        abi: identityAbi,
        eventName: "Registered",
        topics: log.topics,
        data: log.data,
      });
      if (decoded.args.owner.toLowerCase() === expectedOwner.toLowerCase()) {
        return decoded.args.agentId;
      }
    } catch {
      continue;
    }
  }
  throw new Error("ERC-8004 registration receipt is missing the expected Registered event");
}

async function registerAgent(context, args) {
  let agentId;
  let registrationHash = null;
  let registrationBlockNumber = null;
  const existingId = context.env[args.envName]?.trim();
  if (existingId) {
    agentId = BigInt(existingId);
  } else {
    const simulation = await context.publicClient.simulateContract({
      account: context.deployer,
      address: context.identityRegistry,
      abi: identityAbi,
      functionName: "register",
      args: [args.agentUri],
    });
    registrationHash = await context.walletClient.writeContract({
      ...simulation.request,
      gas: 500_000n,
    });
    const receipt = await requireSuccessfulReceipt(
      context.publicClient,
      registrationHash,
      `${args.label} ERC-8004 registration`,
    );
    registrationBlockNumber = receipt.blockNumber;
    agentId = registeredAgentId(receipt, context.identityRegistry, context.deployer.address);
    if (agentId !== simulation.result) {
      throw new Error(`${args.label} simulated and emitted agent IDs differ`);
    }
    await persistEnvValue(args.envName, agentId.toString());
  }

  const owner = await readContractAtConfirmedBlock(context.publicClient, {
    address: context.identityRegistry,
    abi: identityAbi,
    functionName: "ownerOf",
    args: [agentId],
  }, registrationBlockNumber);
  if (owner.toLowerCase() !== context.deployer.address.toLowerCase()) {
    throw new Error(`${args.label} ERC-8004 owner does not match deployer`);
  }

  let agentWallet = await readContractAtConfirmedBlock(context.publicClient, {
    address: context.identityRegistry,
    abi: identityAbi,
    functionName: "getAgentWallet",
    args: [agentId],
  }, registrationBlockNumber);
  let walletBindingHash = null;
  if (agentWallet.toLowerCase() !== args.seller.address.toLowerCase()) {
    const latestBlock = await context.publicClient.getBlock({ blockTag: "latest" });
    const deadline = latestBlock.timestamp + 240n;
    const signature = await args.seller.signTypedData({
      domain: {
        name: "ERC8004IdentityRegistry",
        version: "1",
        chainId: baseSepolia.id,
        verifyingContract: context.identityRegistry,
      },
      types: {
        AgentWalletSet: [
          { name: "agentId", type: "uint256" },
          { name: "newWallet", type: "address" },
          { name: "owner", type: "address" },
          { name: "deadline", type: "uint256" },
        ],
      },
      primaryType: "AgentWalletSet",
      message: {
        agentId,
        newWallet: args.seller.address,
        owner: context.deployer.address,
        deadline,
      },
    });
    walletBindingHash = await context.walletClient.writeContract({
      account: context.deployer,
      address: context.identityRegistry,
      abi: identityAbi,
      functionName: "setAgentWallet",
      args: [agentId, args.seller.address, deadline, signature],
      gas: 150_000n,
    });
    const receipt = await requireSuccessfulReceipt(
      context.publicClient,
      walletBindingHash,
      `${args.label} ERC-8004 wallet binding`,
    );
    agentWallet = await readContractAtConfirmedBlock(context.publicClient, {
      address: context.identityRegistry,
      abi: identityAbi,
      functionName: "getAgentWallet",
      args: [agentId],
    }, receipt.blockNumber);
  }
  if (agentWallet.toLowerCase() !== args.seller.address.toLowerCase()) {
    throw new Error(`${args.label} ERC-8004 agent wallet was not persisted`);
  }
  return {
    agentId: agentId.toString(),
    owner,
    agentWallet,
    registrationHash,
    walletBindingHash,
  };
}

async function status() {
  const context = await runtime();
  async function agentStatus(configuredId, expectedWallet) {
    if (!configuredId) return { configuredAgentId: null, expectedWallet };
    const agentId = BigInt(configuredId);
    const [owner, agentWallet] = await Promise.all([
      context.publicClient.readContract({
        address: context.identityRegistry,
        abi: identityAbi,
        functionName: "ownerOf",
        args: [agentId],
      }),
      context.publicClient.readContract({
        address: context.identityRegistry,
        abi: identityAbi,
        functionName: "getAgentWallet",
        args: [agentId],
      }),
    ]);
    return {
      configuredAgentId: configuredId,
      owner,
      agentWallet,
      expectedWallet,
      ownerMatches: owner.toLowerCase() === context.deployer.address.toLowerCase(),
      walletMatches: agentWallet.toLowerCase() === expectedWallet.toLowerCase(),
    };
  }
  const [identityVersion, reputationVersion, gemini, nemotron] = await Promise.all([
    context.publicClient.readContract({
      address: context.identityRegistry,
      abi: identityAbi,
      functionName: "getVersion",
    }),
    context.publicClient.readContract({
      address: context.reputationRegistry,
      abi: identityAbi,
      functionName: "getVersion",
    }),
    agentStatus(context.env.GEMINI_ERC8004_AGENT_ID?.trim(), context.geminiSeller.address),
    agentStatus(context.env.NEMOTRON_ERC8004_AGENT_ID?.trim(), context.nemotronSeller.address),
  ]);
  const result = {
    network: "Base Sepolia",
    chainId: baseSepolia.id,
    identityRegistry: context.identityRegistry,
    reputationRegistry: context.reputationRegistry,
    identityRegistryVersion: identityVersion,
    reputationRegistryVersion: reputationVersion,
    deployerAddress: context.deployer.address,
    gemini,
    nemotron,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

async function register() {
  const context = await runtime();
  const [identityCode, reputationCode, identityVersion, reputationVersion] = await Promise.all([
    context.publicClient.getBytecode({ address: context.identityRegistry }),
    context.publicClient.getBytecode({ address: context.reputationRegistry }),
    context.publicClient.readContract({
      address: context.identityRegistry,
      abi: identityAbi,
      functionName: "getVersion",
    }),
    context.publicClient.readContract({
      address: context.reputationRegistry,
      abi: identityAbi,
      functionName: "getVersion",
    }),
  ]);
  if (!identityCode || identityCode === "0x" || !reputationCode || reputationCode === "0x") {
    throw new Error("ERC-8004 Identity or Reputation Registry is not deployed");
  }
  if (identityVersion !== "2.0.0" || reputationVersion !== "2.0.0") {
    throw new Error(
      `unsupported ERC-8004 registry versions: identity=${identityVersion}, reputation=${reputationVersion}`,
    );
  }
  await persistEnvValue("ERC8004_IDENTITY_REGISTRY", context.identityRegistry);
  await persistEnvValue("ERC8004_REPUTATION_REGISTRY", context.reputationRegistry);

  const gemini = await registerAgent(context, {
    label: "Gemini",
    envName: "GEMINI_ERC8004_AGENT_ID",
    agentUri: "urn:pbl:seller:gemini",
    seller: context.geminiSeller,
  });
  context.env.GEMINI_ERC8004_AGENT_ID = gemini.agentId;
  const nemotron = await registerAgent(context, {
    label: "Nemotron",
    envName: "NEMOTRON_ERC8004_AGENT_ID",
    agentUri: "urn:pbl:seller:nemotron",
    seller: context.nemotronSeller,
  });
  process.stdout.write(`${JSON.stringify({
    identityRegistry: context.identityRegistry,
    reputationRegistry: context.reputationRegistry,
    registryVersion: identityVersion,
    gemini,
    nemotron,
  }, null, 2)}\n`);
}

const command = process.argv[2] ?? "status";
if (command === "status") await status();
else if (command === "register") await register();
else throw new Error("usage: node scripts/erc8004_setup.mjs [status|register]");
