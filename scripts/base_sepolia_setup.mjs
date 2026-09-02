#!/usr/bin/env node

import { chmod, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createPublicClient,
  createWalletClient,
  formatEther,
  getAddress,
  http,
  parseAbi,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ENV_PATH = resolve(REPO_ROOT, ".env.local");
const DEFAULT_RPC_URL = "https://sepolia.base.org";
const DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator";
const CANONICAL_PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const X402_EXACT_PERMIT2_PROXY = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001";
const INITIAL_SUPPLY_UNITS = 1_000_000n * 1_000_000n;

const erc20Abi = parseAbi([
  "function name() view returns (string)",
  "function symbol() view returns (string)",
  "function owner() view returns (address)",
  "function balanceOf(address account) view returns (uint256)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function nonces(address owner) view returns (uint256)",
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

async function loadArtifact(contractName) {
  const path = resolve(
    REPO_ROOT,
    "infra/contracts/out",
    `${contractName}.sol`,
    `${contractName}.json`,
  );
  const artifact = JSON.parse(await readFile(path, "utf8"));
  if (!Array.isArray(artifact.abi) || !artifact.bytecode?.object?.startsWith("0x")) {
    throw new Error(`invalid Foundry artifact: ${path}`);
  }
  return { abi: artifact.abi, bytecode: artifact.bytecode.object };
}

async function facilitatorCapabilities(url) {
  const response = await fetch(`${url.replace(/\/$/, "")}/supported`);
  if (!response.ok) return { exactV2: false, eip2612GasSponsoring: false };
  const body = await response.json();
  return {
    exactV2: Array.isArray(body.kinds) && body.kinds.some(
      (kind) => kind?.x402Version === 2
        && kind?.scheme === "exact"
        && kind?.network === "eip155:84532",
    ),
    eip2612GasSponsoring:
      Array.isArray(body.extensions) && body.extensions.includes("eip2612GasSponsoring"),
  };
}

async function runtime() {
  const envText = await readFile(ENV_PATH, "utf8");
  const env = parseEnv(envText);
  const rpcUrl = env.BASE_SEPOLIA_RPC_URL?.trim() || DEFAULT_RPC_URL;
  const facilitatorUrl = env.FACILITATOR_URL?.trim() || DEFAULT_FACILITATOR_URL;
  const deployer = privateKeyToAccount(required(env, "DEPLOYER_PRIVATE_KEY"));
  const buyer = privateKeyToAccount(required(env, "BUYER_AGENT_PRIVATE_KEY"));
  const geminiSeller = privateKeyToAccount(required(env, "GEMINI_SELLER_PRIVATE_KEY"));
  const nemotronSeller = privateKeyToAccount(required(env, "NEMOTRON_SELLER_PRIVATE_KEY"));
  const publicClient = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });
  const walletClient = createWalletClient({
    account: deployer,
    chain: baseSepolia,
    transport: http(rpcUrl),
  });
  const chainId = await publicClient.getChainId();
  if (chainId !== baseSepolia.id) throw new Error(`wrong chain id: ${chainId}`);
  return {
    env,
    envText,
    rpcUrl,
    facilitatorUrl,
    deployer,
    buyer,
    geminiSeller,
    nemotronSeller,
    publicClient,
    walletClient,
  };
}

async function status() {
  const context = await runtime();
  const [buyerBalance, deployerBalance, permit2Code, exactProxyCode, capabilities] =
    await Promise.all([
      context.publicClient.getBalance({ address: context.buyer.address }),
      context.publicClient.getBalance({ address: context.deployer.address }),
      context.publicClient.getBytecode({ address: CANONICAL_PERMIT2 }),
      context.publicClient.getBytecode({ address: X402_EXACT_PERMIT2_PROXY }),
      facilitatorCapabilities(context.facilitatorUrl),
    ]);
  const result = {
    network: "Base Sepolia",
    chainId: baseSepolia.id,
    rpcUrl: context.rpcUrl,
    facilitatorUrl: context.facilitatorUrl,
    facilitatorSupportsExactV2: capabilities.exactV2,
    facilitatorSupportsEip2612GasSponsoring: capabilities.eip2612GasSponsoring,
    canonicalPermit2Deployed: Boolean(permit2Code && permit2Code !== "0x"),
    x402ExactPermit2ProxyDeployed: Boolean(exactProxyCode && exactProxyCode !== "0x"),
    deployerAddress: context.deployer.address,
    deployerEth: formatEther(deployerBalance),
    buyerAddress: context.buyer.address,
    buyerEth: formatEther(buyerBalance),
    geminiSellerAddress: context.geminiSeller.address,
    nemotronSellerAddress: context.nemotronSeller.address,
    tokenAddress: context.env.TOKEN_ADDRESS || null,
    evidenceAnchorAddress: context.env.EVIDENCE_ANCHOR_ADDRESS || null,
  };
  if (context.env.TOKEN_ADDRESS) {
    result.buyerTokenUnits = String(await context.publicClient.readContract({
      address: context.env.TOKEN_ADDRESS,
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [context.buyer.address],
    }));
    result.permit2AllowanceUnits = String(await context.publicClient.readContract({
      address: context.env.TOKEN_ADDRESS,
      abi: erc20Abi,
      functionName: "allowance",
      args: [context.buyer.address, CANONICAL_PERMIT2],
    }));
    result.buyerPermitNonce = String(await context.publicClient.readContract({
      address: context.env.TOKEN_ADDRESS,
      abi: erc20Abi,
      functionName: "nonces",
      args: [context.buyer.address],
    }));
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

async function requireSuccessfulReceipt(publicClient, hash, label) {
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`${label} transaction reverted: ${hash}`);
  return receipt;
}

async function registerBuyerWriter(context, anchorAddress, anchorAbi) {
  const alreadyRegistered = await context.publicClient.readContract({
    address: anchorAddress,
    abi: anchorAbi,
    functionName: "isWriter",
    args: [context.buyer.address],
  });
  if (alreadyRegistered === true) return null;

  const hash = await context.walletClient.writeContract({
    account: context.deployer,
    address: anchorAddress,
    abi: anchorAbi,
    functionName: "setWriter",
    args: [context.buyer.address, true],
    gas: 100_000n,
  });
  const receipt = await requireSuccessfulReceipt(
    context.publicClient,
    hash,
    "EvidenceAnchor buyer writer registration",
  );
  const registered = await context.publicClient.readContract({
    address: anchorAddress,
    abi: anchorAbi,
    functionName: "isWriter",
    args: [context.buyer.address],
    blockNumber: receipt.blockNumber,
  });
  if (registered !== true) {
    throw new Error("EvidenceAnchor buyer writer registration was not persisted");
  }
  return hash;
}

async function persistDeploymentEnv(context, tokenAddress, anchorAddress) {
  let nextEnv = context.envText;
  const updates = {
    BASE_SEPOLIA_RPC_URL: context.rpcUrl,
    TOKEN_ADDRESS: tokenAddress,
    QUOTE_VERIFYING_CONTRACT: tokenAddress,
    DECISION_VERIFYING_CONTRACT: anchorAddress,
    EVIDENCE_ANCHOR_ADDRESS: anchorAddress,
    GEMINI_PAY_TO_ADDRESS: context.geminiSeller.address,
    NEMOTRON_PAY_TO_ADDRESS: context.nemotronSeller.address,
  };
  for (const [name, value] of Object.entries(updates)) {
    nextEnv = replaceEnvValue(nextEnv, name, value);
  }
  await writeFile(ENV_PATH, nextEnv, { encoding: "utf8", mode: 0o600 });
  await chmod(ENV_PATH, 0o600);
}

async function deploy() {
  const context = await runtime();
  if (context.env.TOKEN_ADDRESS || context.env.EVIDENCE_ANCHOR_ADDRESS) {
    throw new Error("deployment addresses already exist; refusing duplicate deployment");
  }
  const capabilities = await facilitatorCapabilities(context.facilitatorUrl);
  if (!capabilities.exactV2 || !capabilities.eip2612GasSponsoring) {
    throw new Error(
      "facilitator does not advertise Base Sepolia exact v2 with EIP-2612 gas sponsorship",
    );
  }
  const [permit2Code, exactProxyCode] = await Promise.all([
    context.publicClient.getBytecode({ address: CANONICAL_PERMIT2 }),
    context.publicClient.getBytecode({ address: X402_EXACT_PERMIT2_PROXY }),
  ]);
  if (!permit2Code || permit2Code === "0x" || !exactProxyCode || exactProxyCode === "0x") {
    throw new Error("canonical Permit2 or x402 exact Permit2 proxy is not deployed");
  }
  const balance = await context.publicClient.getBalance({ address: context.deployer.address });
  if (balance === 0n) {
    throw new Error(`deployer wallet needs Base Sepolia ETH: ${context.deployer.address}`);
  }

  const tokenArtifact = await loadArtifact("DemoToken");
  const tokenDeploymentHash = await context.walletClient.deployContract({
    account: context.deployer,
    abi: tokenArtifact.abi,
    bytecode: tokenArtifact.bytecode,
    args: [context.deployer.address, context.buyer.address, INITIAL_SUPPLY_UNITS],
  });
  const tokenReceipt = await requireSuccessfulReceipt(
    context.publicClient,
    tokenDeploymentHash,
    "DemoToken deployment",
  );
  if (!tokenReceipt.contractAddress) throw new Error("DemoToken address is missing");

  const anchorArtifact = await loadArtifact("EvidenceAnchor");
  const anchorDeploymentHash = await context.walletClient.deployContract({
    account: context.deployer,
    abi: anchorArtifact.abi,
    bytecode: anchorArtifact.bytecode,
    args: [context.deployer.address],
  });
  const anchorReceipt = await requireSuccessfulReceipt(
    context.publicClient,
    anchorDeploymentHash,
    "EvidenceAnchor deployment",
  );
  if (!anchorReceipt.contractAddress) throw new Error("EvidenceAnchor address is missing");

  const writerRegistrationHash = await registerBuyerWriter(
    context,
    anchorReceipt.contractAddress,
    anchorArtifact.abi,
  );
  await persistDeploymentEnv(
    context,
    tokenReceipt.contractAddress,
    anchorReceipt.contractAddress,
  );

  process.stdout.write(`${JSON.stringify({
    deployerAddress: context.deployer.address,
    buyerAddress: context.buyer.address,
    tokenAddress: tokenReceipt.contractAddress,
    tokenDeploymentHash,
    evidenceAnchorAddress: anchorReceipt.contractAddress,
    anchorDeploymentHash,
    writerRegistrationHash,
    permit2Address: CANONICAL_PERMIT2,
    x402ExactPermit2Proxy: X402_EXACT_PERMIT2_PROXY,
    initialBuyerTokenUnits: INITIAL_SUPPLY_UNITS.toString(),
    buyerNeedsNativeEth: false,
  }, null, 2)}\n`);
}

async function recover(tokenAddressInput, anchorAddressInput) {
  const context = await runtime();
  if (context.env.TOKEN_ADDRESS || context.env.EVIDENCE_ANCHOR_ADDRESS) {
    throw new Error("deployment addresses already exist; recovery is unnecessary");
  }
  if (!tokenAddressInput || !anchorAddressInput) {
    throw new Error("recover requires token and EvidenceAnchor addresses");
  }
  const tokenAddress = getAddress(tokenAddressInput);
  const anchorAddress = getAddress(anchorAddressInput);
  const anchorArtifact = await loadArtifact("EvidenceAnchor");
  const [tokenCode, anchorCode, tokenName, tokenSymbol, tokenOwner, buyerBalance, anchorOwner] =
    await Promise.all([
      context.publicClient.getBytecode({ address: tokenAddress }),
      context.publicClient.getBytecode({ address: anchorAddress }),
      context.publicClient.readContract({
        address: tokenAddress,
        abi: erc20Abi,
        functionName: "name",
      }),
      context.publicClient.readContract({
        address: tokenAddress,
        abi: erc20Abi,
        functionName: "symbol",
      }),
      context.publicClient.readContract({
        address: tokenAddress,
        abi: erc20Abi,
        functionName: "owner",
      }),
      context.publicClient.readContract({
        address: tokenAddress,
        abi: erc20Abi,
        functionName: "balanceOf",
        args: [context.buyer.address],
      }),
      context.publicClient.readContract({
        address: anchorAddress,
        abi: anchorArtifact.abi,
        functionName: "owner",
      }),
    ]);
  if (!tokenCode || tokenCode === "0x" || !anchorCode || anchorCode === "0x") {
    throw new Error("recovery addresses must contain deployed contracts");
  }
  if (tokenName !== "PBL Agent Credit" || tokenSymbol !== "PBLC") {
    throw new Error("token recovery address is not PBLC");
  }
  if (
    tokenOwner.toLowerCase() !== context.deployer.address.toLowerCase()
    || anchorOwner.toLowerCase() !== context.deployer.address.toLowerCase()
  ) {
    throw new Error("recovery contract owner does not match deployer");
  }
  if (buyerBalance !== INITIAL_SUPPLY_UNITS) {
    throw new Error("recovery PBLC buyer balance does not match initial supply");
  }

  const writerRegistrationHash = await registerBuyerWriter(
    context,
    anchorAddress,
    anchorArtifact.abi,
  );
  await persistDeploymentEnv(context, tokenAddress, anchorAddress);
  process.stdout.write(`${JSON.stringify({
    recovered: true,
    tokenAddress,
    evidenceAnchorAddress: anchorAddress,
    writerRegistrationHash,
    buyerAddress: context.buyer.address,
    initialBuyerTokenUnits: buyerBalance.toString(),
  }, null, 2)}\n`);
}

const command = process.argv[2] ?? "status";
if (command === "status") await status();
else if (command === "deploy") await deploy();
else if (command === "recover") await recover(process.argv[3], process.argv[4]);
else throw new Error("usage: node scripts/base_sepolia_setup.mjs [status|deploy|recover]");
