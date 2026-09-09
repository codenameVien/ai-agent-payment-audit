import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { keccak256 } from "viem";

import {
  ARTIFACT_PATH,
  SOURCE_PATH,
  UNKNOWN,
  buildPlan,
  intrinsicGas,
  parseArgs,
  readTokenMetadata,
} from "./aegis_token_plan.mjs";

const DEPLOYER = "0x5B2BC76a3e4DeA700309FD9D746180162bcAbec8";
const HOLDER = "0xa45Cd1a41E1e548e2daB0123E7Cb4E3dB964cdaB";
const BASE_ARGV = argvFor();

function argvFor({ nonce = "0", supply = "1000000000000", extra = [] } = {}) {
  return [
    "--deployer",
    DEPLOYER,
    "--holder",
    HOLDER,
    "--nonce",
    nonce,
    "--initial-supply-units",
    supply,
    ...extra,
  ];
}

const source = await readFile(SOURCE_PATH, "utf8");
const scriptSource = await readFile(new URL("./aegis_token_plan.mjs", import.meta.url), "utf8");
const artifact = JSON.parse(await readFile(ARTIFACT_PATH, "utf8"));

/** Independent CREATE derivation: keccak(rlp([sender, nonce]))[12:]. */
function createAddress(sender, nonce) {
  const senderBytes = sender.slice(2).toLowerCase();
  let nonceItem;
  if (nonce === 0n) nonceItem = "80";
  else if (nonce < 0x80n) nonceItem = nonce.toString(16).padStart(2, "0");
  else {
    const hex = nonce.toString(16);
    const padded = hex.length % 2 === 1 ? `0${hex}` : hex;
    nonceItem = (0x80 + padded.length / 2).toString(16) + padded;
  }
  const payload = `94${senderBytes}${nonceItem}`;
  const listPrefix = (0xc0 + payload.length / 2).toString(16);
  return `0x${keccak256(`0x${listPrefix}${payload}`).slice(-40)}`;
}

function plan(overrides) {
  return buildPlan({ artifact, source, options: parseArgs(argvFor(overrides)) });
}

test("planner source cannot reach the network, a key store or a broadcast path", () => {
  for (const forbidden of [
    "process.env",
    "fetch(",
    "createPublicClient",
    "createWalletClient",
    "deployContract",
    "privateKeyToAccount",
    "sendTransaction",
    ".env.local",
    "node:child_process",
    "writeFile",
  ]) {
    assert.ok(
      !scriptSource.includes(forbidden),
      `plan-only script must not reference ${forbidden}`,
    );
  }
  assert.deepEqual(
    [...scriptSource.matchAll(/from "([^"]+)"/g)].map((match) => match[1]),
    ["node:fs/promises", "node:path", "node:url", "viem"],
  );
});

test("private-key shaped values and network or broadcast flags are refused", () => {
  const key = `0x${"ab".repeat(32)}`;
  assert.throws(() => parseArgs([...BASE_ARGV, "--owner", key]), /looks like a private key/);
  assert.throws(() => parseArgs(["--private-key", "x", ...BASE_ARGV]), /refused flag/);
  assert.throws(() => parseArgs(["--rpc-url", "http://localhost:8545", ...BASE_ARGV]), /refused/);
  assert.throws(() => parseArgs([...BASE_ARGV, "--broadcast", "yes"]), /refused flag/);
  assert.throws(() => parseArgs([...BASE_ARGV, "--mnemonic", "test test"]), /refused flag/);
});

test("every deployment determining input must be supplied explicitly", () => {
  for (const missing of ["--deployer", "--holder", "--nonce", "--initial-supply-units"]) {
    const argv = [];
    for (let index = 0; index < BASE_ARGV.length; index += 2) {
      if (BASE_ARGV[index] !== missing) argv.push(BASE_ARGV[index], BASE_ARGV[index + 1]);
    }
    assert.throws(() => parseArgs(argv), new RegExp(`${missing} is required`));
  }
  assert.throws(() => parseArgs([...BASE_ARGV, "--gas", "1"]), /unknown flag --gas/);
  assert.throws(() => parseArgs([...BASE_ARGV, "--nonce", "1"]), /--nonce was given twice/);
  assert.throws(() => parseArgs([...BASE_ARGV, "--owner", "0x1234"]), /must be a 20-byte/);
  assert.throws(
    () => parseArgs([...BASE_ARGV, "--owner", `0x${"0".repeat(40)}`]),
    /must not be the zero address/,
  );
  assert.throws(
    () => parseArgs(["--deployer", DEPLOYER, "--holder", HOLDER, "--nonce", "1.5",
      "--initial-supply-units", "1"]),
    /--nonce must be a non-negative decimal integer/,
  );
  assert.throws(
    () => parseArgs(["--deployer", DEPLOYER, "--holder", HOLDER, "--nonce", "1",
      "--initial-supply-units", "1e6"]),
    /--initial-supply-units must be a non-negative decimal integer/,
  );
});

test("token metadata is read from the contract source, never hardcoded", () => {
  const metadata = readTokenMetadata(source);
  assert.deepEqual(
    { name: metadata.name, symbol: metadata.symbol, decimals: metadata.decimals },
    { name: "AEGIS", symbol: "AEGIS", decimals: 6 },
  );
  assert.equal(metadata.eip712Version, "1");
  assert.equal(metadata.upgradeable, false);
  assert.equal(metadata.nonUpgradeableEvidence.length, 2);

  const doctored = `${source}\nfunction initialize(address a) external {}\n`;
  const doctoredMetadata = readTokenMetadata(doctored);
  assert.equal(doctoredMetadata.upgradeable, true);
  assert.deepEqual(doctoredMetadata.nonUpgradeableEvidence, ["found proxy initializer"]);

  assert.throws(
    () => readTokenMetadata(source.replace('string public constant symbol = "AEGIS";', "")),
    /could not read constant "symbol"/,
  );
});

test("intrinsic transaction gas is the 21000 base plus EIP-2028 calldata cost only", () => {
  assert.deepEqual(intrinsicGas("0x00ff"), {
    zeroBytes: 1,
    nonZeroBytes: 1,
    gas: 21000n + 4n + 16n,
  });
  const result = plan().gas;
  const calldataBytes = plan().deployment.creationCalldataBytes;
  assert.ok(BigInt(result.intrinsicTxGas) > 21000n);
  assert.ok(BigInt(result.intrinsicTxGas) <= 21000n + BigInt(calldataBytes) * 16n);
  assert.match(result.intrinsicTxGasBasis, /21000 tx base/);
});

test("predicted CREATE address matches an independent RLP derivation and follows the nonce", () => {
  for (const nonce of ["0", "1", "7", "128", "1000"]) {
    const deployment = plan({ nonce }).deployment;
    assert.equal(
      deployment.predictedAddress.toLowerCase(),
      createAddress(DEPLOYER, BigInt(nonce)),
      `nonce ${nonce} address mismatch`,
    );
  }
  assert.notEqual(
    plan({ nonce: "0" }).deployment.predictedAddress,
    plan({ nonce: "1" }).deployment.predictedAddress,
  );
  assert.equal(plan().deployment.predictedAddress, plan().deployment.predictedAddress);
});

test("creation calldata is the artifact bytecode plus the three encoded constructor arguments", () => {
  const result = plan();
  const bytecode = artifact.bytecode.object;
  assert.equal(result.deployment.creationCalldataBytes, (bytecode.length - 2) / 2 + 96);
  assert.deepEqual(result.artifact.constructorInputs, [
    "address initialOwner",
    "address initialHolder",
    "uint256 initialSupplyUnits",
  ]);
  assert.equal(result.artifact.creationBytecodeHash, keccak256(bytecode));
  assert.equal(result.deployment.initialSupplyDisplay, "1000000 AEGIS");
  assert.equal(result.deployment.initialOwner, DEPLOYER);
  assert.equal(result.deployment.initialOwnerSource, "defaulted to --deployer");
});

test("unmeasured gas, unquoted fee and unsupplied chain id stay UNKNOWN", () => {
  const bare = plan();
  assert.equal(bare.gas.creationFrameGas, UNKNOWN);
  assert.equal(bare.gas.creationFrameGasSource, UNKNOWN);
  assert.equal(bare.gas.totalDeploymentGas, UNKNOWN);
  assert.equal(bare.gas.maxFeePerGasWei, UNKNOWN);
  assert.equal(bare.gas.maxDeploymentCostWei, UNKNOWN);
  assert.equal(bare.gas.maxDeploymentCostEth, UNKNOWN);
  assert.equal(bare.deployment.chainId, UNKNOWN);
  assert.equal(bare.deployment.chainIdSource, UNKNOWN);
  assert.notEqual(bare.gas.intrinsicTxGas, UNKNOWN);
});

test("supplied local measurements are labelled and multiplied without a live quote", () => {
  const measured = plan({
    extra: [
      "--local-creation-gas",
      "745062",
      "--local-creation-gas-source",
      "forge test testDeploymentCreationGasMeasuredLocally",
      "--max-fee-per-gas-wei",
      "7000",
      "--chain-id",
      "84532",
    ],
  });
  const total = BigInt(measured.gas.intrinsicTxGas) + 745062n;
  assert.equal(measured.gas.totalDeploymentGas, total.toString());
  assert.equal(measured.gas.maxDeploymentCostWei, (total * 7000n).toString());
  assert.equal(
    measured.gas.creationFrameGasSource,
    "forge test testDeploymentCreationGasMeasuredLocally",
  );
  assert.match(measured.gas.maxFeePerGasSource, /no live gas quote/);
  assert.match(measured.gas.totalDeploymentGasBasis, /not an eth_estimateGas result/);
  assert.match(measured.deployment.chainIdSource, /not verified against any RPC/);
});

test("the plan is marked plan-only, approval pending, with a suggestion-only test payment", () => {
  const result = plan({ extra: ["--test-payment-units", "100000"] });
  assert.equal(result.action, "PLAN_ONLY_NO_TRANSACTION_SENT");
  assert.equal(result.approval.status, "PENDING_USER_APPROVAL");
  assert.deepEqual(result.approval.blockedUntilApproved, [
    "contract deployment",
    "asset transfer",
    "real testnet payment",
  ]);
  assert.deepEqual(result.suggestedFirstTestPayment, {
    units: "100000",
    display: "0.1 AEGIS",
    status: "SUGGESTION_ONLY_NOT_EXECUTED",
  });
  assert.match(result.notes.join("\n"), /nominal accounting conversion/);
  assert.match(result.notes.join("\n"), /PBLC V1\/V2 contracts, addresses and transactions/);
  assert.match(result.deployment.method, /no factory, no CREATE2/);
});

test("a missing or empty artifact fails loudly instead of guessing", () => {
  const options = parseArgs(BASE_ARGV);
  assert.throws(
    () => buildPlan({ artifact: { abi: artifact.abi, bytecode: { object: "0x" } }, source, options }),
    /run: forge build --root infra\/contracts/,
  );
  assert.throws(
    () => buildPlan({ artifact: { abi: [], bytecode: artifact.bytecode }, source, options }),
    /artifact has no constructor/,
  );
  assert.throws(() => buildPlan({ artifact: {}, source, options }), /no creation bytecode/);
});
