#!/usr/bin/env node

/**
 * AEGIS token deployment planner — PLAN ONLY.
 *
 * Computes the approval packet for a future AEGIS deployment from explicit public inputs:
 * predicted CREATE address, creation calldata, and gas arithmetic over locally measured numbers.
 *
 * Hard boundaries, enforced by construction and by scripts/aegis_token_plan.test.mjs:
 * - no deploy, no broadcast, no signing;
 * - no network access (no RPC client, no HTTP transport, no gas oracle);
 * - no private-key input, no dotenv file read, no environment variable read;
 * - anything not supplied and not locally computable is reported as "UNKNOWN", never invented.
 */

import { readFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { encodeDeployData, formatUnits, getAddress, getContractAddress, keccak256 } from "viem";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export const SOURCE_PATH = resolve(REPO_ROOT, "infra/contracts/src/AEGISToken.sol");
export const ARTIFACT_PATH = resolve(
  REPO_ROOT,
  "infra/contracts/out/AEGISToken.sol/AEGISToken.json",
);
export const UNKNOWN = "UNKNOWN";

const PRIVATE_KEY_SHAPE = /^0x?[0-9a-fA-F]{64}$/;
const REQUIRED_FLAGS = ["deployer", "holder", "nonce", "initial-supply-units"];
const OPTIONAL_FLAGS = [
  "owner",
  "chain-id",
  "local-creation-gas",
  "local-creation-gas-source",
  "max-fee-per-gas-wei",
  "max-fee-per-gas-source",
  "test-payment-units",
];
const KNOWN_FLAGS = new Set([...REQUIRED_FLAGS, ...OPTIONAL_FLAGS]);
const BANNED_FLAGS = new Set([
  "private-key",
  "deployer-private-key",
  "key",
  "mnemonic",
  "rpc-url",
  "rpc",
  "broadcast",
  "deploy",
  "env-file",
]);

export const USAGE = `Usage (plan only, offline):
  node scripts/aegis_token_plan.mjs \\
    --deployer <public 0x address> \\
    --holder <public 0x address> \\
    --nonce <deployer account nonce at send time> \\
    --initial-supply-units <integer, 6 decimals> \\
    [--owner <public 0x address, defaults to deployer>] \\
    [--chain-id <operator supplied chain id>] \\
    [--local-creation-gas <gas measured by forge test>] \\
    [--local-creation-gas-source <label>] \\
    [--max-fee-per-gas-wei <operator supplied assumption>] \\
    [--max-fee-per-gas-source <label>] \\
    [--test-payment-units <suggested first payment, default 100000>]

This command never deploys, never signs and never contacts a network.`;

function fail(message) {
  throw new Error(message);
}

export function parseArgs(argv) {
  const raw = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) fail(`unexpected argument: ${token}`);
    const equals = token.indexOf("=");
    const flag = (equals === -1 ? token.slice(2) : token.slice(2, equals)).toLowerCase();
    let value;
    if (equals === -1) {
      value = argv[index + 1];
      index += 1;
    } else {
      value = token.slice(equals + 1);
    }
    if (BANNED_FLAGS.has(flag)) {
      fail(`refused flag --${flag}: this planner is offline and never takes keys or endpoints`);
    }
    if (!KNOWN_FLAGS.has(flag)) fail(`unknown flag --${flag}`);
    if (value === undefined || value === "") fail(`--${flag} requires a value`);
    if (PRIVATE_KEY_SHAPE.test(value)) {
      fail(`--${flag} looks like a private key; pass public values only`);
    }
    if (raw[flag] !== undefined) fail(`--${flag} was given twice`);
    raw[flag] = value;
  }
  for (const flag of REQUIRED_FLAGS) {
    if (raw[flag] === undefined) fail(`--${flag} is required\n\n${USAGE}`);
  }
  return {
    deployer: address(raw.deployer, "--deployer"),
    holder: address(raw.holder, "--holder"),
    owner: raw.owner === undefined ? null : address(raw.owner, "--owner"),
    nonce: unsignedInteger(raw.nonce, "--nonce"),
    initialSupplyUnits: unsignedInteger(raw["initial-supply-units"], "--initial-supply-units"),
    chainId: raw["chain-id"] === undefined ? null : unsignedInteger(raw["chain-id"], "--chain-id"),
    localCreationGas:
      raw["local-creation-gas"] === undefined
        ? null
        : unsignedInteger(raw["local-creation-gas"], "--local-creation-gas"),
    localCreationGasSource: raw["local-creation-gas-source"] ?? null,
    maxFeePerGasWei:
      raw["max-fee-per-gas-wei"] === undefined
        ? null
        : unsignedInteger(raw["max-fee-per-gas-wei"], "--max-fee-per-gas-wei"),
    maxFeePerGasSource: raw["max-fee-per-gas-source"] ?? null,
    testPaymentUnits:
      raw["test-payment-units"] === undefined
        ? 100000n
        : unsignedInteger(raw["test-payment-units"], "--test-payment-units"),
  };
}

function address(value, flag) {
  try {
    const checksummed = getAddress(value);
    if (checksummed === "0x0000000000000000000000000000000000000000") {
      fail(`${flag} must not be the zero address`);
    }
    return checksummed;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith(flag)) throw error;
    fail(`${flag} must be a 20-byte 0x address, got: ${value}`);
  }
}

function unsignedInteger(value, flag) {
  if (!/^(0|[1-9][0-9]*)$/.test(value)) {
    fail(`${flag} must be a non-negative decimal integer, got: ${value}`);
  }
  return BigInt(value);
}

export function readTokenMetadata(source, { sourcePath = SOURCE_PATH } = {}) {
  const stringConstant = (field) =>
    new RegExp(`string public constant ${field}\\s*=\\s*"([^"]*)";`).exec(source)?.[1];
  const name = stringConstant("name");
  const symbol = stringConstant("symbol");
  const eip712Version = stringConstant("version");
  const decimals = /uint8 public constant decimals\s*=\s*(\d+);/.exec(source)?.[1];
  for (const [field, value] of Object.entries({ name, symbol, eip712Version, decimals })) {
    if (value === undefined) {
      fail(`could not read constant "${field}" from ${relative(REPO_ROOT, sourcePath)}`);
    }
  }
  const mutabilityFindings = [
    ["delegatecall", /delegatecall/],
    ["proxy initializer", /function\s+initialize\s*\(/],
    ["metadata setter", /function\s+set(Name|Symbol|Version|Decimals)\s*\(/],
    ["upgrade hook", /function\s+(upgradeTo|_authorizeUpgrade)\s*\(/],
  ]
    .filter(([, pattern]) => pattern.test(source))
    .map(([label]) => label);
  return {
    name,
    symbol,
    eip712Version,
    decimals: Number(decimals),
    upgradeable: mutabilityFindings.length > 0,
    nonUpgradeableEvidence:
      mutabilityFindings.length > 0
        ? mutabilityFindings.map((label) => `found ${label}`)
        : [
            "name/symbol/version/decimals are compile-time constants with no setter",
            "no delegatecall, proxy initializer or upgrade hook in source",
          ],
    // The reusable planner also prepares the user-owned PBLC contract; retain the
    // caller's exact source path instead of relabelling it as the AEGIS contract.
    source: relative(REPO_ROOT, sourcePath),
  };
}

/**
 * Transaction-level gas that a Foundry CREATE-frame measurement does not contain:
 * the 21000 tx base cost and the EIP-2028 calldata cost of the creation payload.
 * The 32000 create charge and the EIP-3860 initcode word cost are already inside the
 * measured CREATE frame, so they are deliberately not added a second time here.
 */
export function intrinsicGas(calldata) {
  const bytes = calldata.slice(2);
  let zero = 0;
  let nonZero = 0;
  for (let index = 0; index < bytes.length; index += 2) {
    if (bytes.slice(index, index + 2) === "00") zero += 1;
    else nonZero += 1;
  }
  return {
    zeroBytes: zero,
    nonZeroBytes: nonZero,
    gas: 21000n + BigInt(zero) * 4n + BigInt(nonZero) * 16n,
  };
}

export function buildPlan({
  artifact,
  source,
  options,
  sourcePath = SOURCE_PATH,
  artifactPath = ARTIFACT_PATH,
  historicalAssetsNote = "Existing PBLC V1/V2 contracts, addresses and transactions are untouched; AEGIS is a separate deployment.",
}) {
  const token = readTokenMetadata(source, { sourcePath });
  const bytecode = artifact?.bytecode?.object;
  if (typeof bytecode !== "string" || !bytecode.startsWith("0x") || bytecode.length <= 2) {
    fail("artifact has no creation bytecode; run: forge build --root infra/contracts");
  }
  const abi = artifact.abi;
  if (!Array.isArray(abi)) fail("artifact has no abi");
  const constructorInputs =
    abi.find((entry) => entry.type === "constructor")?.inputs?.map((input) =>
      `${input.type} ${input.name}`,
    ) ?? fail("artifact has no constructor");
  const owner = options.owner ?? options.deployer;
  const calldata = encodeDeployData({
    abi,
    bytecode,
    args: [owner, options.holder, options.initialSupplyUnits],
  });
  const intrinsic = intrinsicGas(calldata);
  const creationGas = options.localCreationGas;
  const totalGas = creationGas === null ? null : intrinsic.gas + creationGas;
  const maxFee = options.maxFeePerGasWei;
  const maxCostWei = totalGas === null || maxFee === null ? null : totalGas * maxFee;
  const display = (units) => `${formatUnits(units, token.decimals)} ${token.symbol}`;

  return {
    action: "PLAN_ONLY_NO_TRANSACTION_SENT",
    approval: {
      status: "PENDING_USER_APPROVAL",
      blockedUntilApproved: ["contract deployment", "asset transfer", "real testnet payment"],
      note: "This planner cannot deploy, sign or reach a network; approval is a separate human step.",
    },
    token: {
      name: token.name,
      symbol: token.symbol,
      decimals: token.decimals,
      eip712DomainVersion: token.eip712Version,
      upgradeable: token.upgradeable,
      nonUpgradeableEvidence: token.nonUpgradeableEvidence,
      source: token.source,
      standards: ["ERC-20", "ERC-3009 transferWithAuthorization (x402 v2 exact)"],
    },
    artifact: {
      path: relative(REPO_ROOT, artifactPath),
      compiler: artifact.metadata?.compiler?.version ?? UNKNOWN,
      constructorInputs,
      creationBytecodeBytes: (bytecode.length - 2) / 2,
      creationBytecodeHash: keccak256(bytecode),
    },
    deployment: {
      method: "plain CREATE from an EOA deployment transaction (no factory, no CREATE2)",
      deployer: options.deployer,
      deployerNonceAtSend: options.nonce.toString(),
      predictedAddress: getContractAddress({ from: options.deployer, nonce: options.nonce }),
      predictedAddressCaveat:
        "CREATE address depends on the deployer nonce; recompute if any other transaction lands first",
      initialOwner: owner,
      initialOwnerSource: options.owner === null ? "defaulted to --deployer" : "explicit --owner",
      initialHolder: options.holder,
      initialSupplyUnits: options.initialSupplyUnits.toString(),
      initialSupplyDisplay: display(options.initialSupplyUnits),
      creationCalldataBytes: (calldata.length - 2) / 2,
      creationCalldataHash: keccak256(calldata),
      chainId: options.chainId === null ? UNKNOWN : options.chainId.toString(),
      chainIdSource:
        options.chainId === null ? UNKNOWN : "operator supplied; not verified against any RPC",
    },
    gas: {
      creationFrameGas: creationGas === null ? UNKNOWN : creationGas.toString(),
      creationFrameGasSource:
        creationGas === null
          ? UNKNOWN
          : (options.localCreationGasSource ??
            "operator supplied; label the local measurement with --local-creation-gas-source"),
      intrinsicTxGas: intrinsic.gas.toString(),
      intrinsicTxGasBasis: `locally computed: 21000 tx base + ${intrinsic.nonZeroBytes} non-zero calldata bytes x 16 + ${intrinsic.zeroBytes} zero calldata bytes x 4`,
      totalDeploymentGas: totalGas === null ? UNKNOWN : totalGas.toString(),
      totalDeploymentGasBasis:
        totalGas === null
          ? UNKNOWN
          : "intrinsicTxGas + creationFrameGas; the 32000 create charge and EIP-3860 initcode word cost are inside creationFrameGas. Local arithmetic over a Foundry measurement, not an eth_estimateGas result",
      maxFeePerGasWei: maxFee === null ? UNKNOWN : maxFee.toString(),
      maxFeePerGasSource:
        maxFee === null
          ? UNKNOWN
          : (options.maxFeePerGasSource ?? "operator supplied assumption; no live gas quote"),
      maxDeploymentCostWei: maxCostWei === null ? UNKNOWN : maxCostWei.toString(),
      maxDeploymentCostEth: maxCostWei === null ? UNKNOWN : formatUnits(maxCostWei, 18),
    },
    suggestedFirstTestPayment: {
      units: options.testPaymentUnits.toString(),
      display: display(options.testPaymentUnits),
      status: "SUGGESTION_ONLY_NOT_EXECUTED",
    },
    notes: [
      `1 ${token.symbol} = 1 USD is a nominal accounting conversion; it is not backing, collateral or a redemption promise.`,
      historicalAssetsNote,
      "No RPC query was performed: deployer balance, live nonce and live gas price are outside this planner.",
      "Wallets and nonces must be real approved values at send time; sample values are test-only.",
    ],
  };
}

export async function loadPlanInputs({
  sourcePath = SOURCE_PATH,
  artifactPath = ARTIFACT_PATH,
} = {}) {
  const [source, artifactText] = await Promise.all([
    readFile(sourcePath, "utf8"),
    readFile(artifactPath, "utf8").catch(() => {
      fail(
        `missing ${relative(REPO_ROOT, artifactPath)}; run: forge build --root infra/contracts`,
      );
    }),
  ]);
  return { source, artifact: JSON.parse(artifactText) };
}

async function main(argv) {
  const options = parseArgs(argv);
  const { source, artifact } = await loadPlanInputs();
  const plan = buildPlan({ artifact, source, options });
  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    await main(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
