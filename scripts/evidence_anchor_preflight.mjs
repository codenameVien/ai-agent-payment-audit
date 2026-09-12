// Read-only Base Sepolia deployment plan. Never reads a key, signs or broadcasts.
import { readFile } from "node:fs/promises";
import { createPublicClient, encodeDeployData, formatEther, getContractAddress, http, isAddress } from "viem";
const writer = process.argv[2];
if (!writer || !isAddress(writer)) throw new Error("Supply the public writer address as the only argument");
const artifact = JSON.parse(await readFile(new URL("../infra/contracts/out/EvidenceAnchor.sol/EvidenceAnchor.json", import.meta.url), "utf8"));
const client = createPublicClient({ transport: http("https://sepolia.base.org") });
if (await client.getChainId() !== 84532) throw new Error("unexpected network");
const nonce = await client.getTransactionCount({ address: writer, blockTag: "pending" });
const data = encodeDeployData({ abi: artifact.abi, bytecode: artifact.bytecode.object, args: [writer] });
const gas = await client.estimateGas({ account: writer, data });
const fee = await client.estimateFeesPerGas();
console.log(JSON.stringify({ mode: "read-only-plan", broadcast: false, chainId: 84532, writer,
  deployMethod: "CREATE", nonce, expectedAddress: getContractAddress({ from: writer, nonce: BigInt(nonce) }),
  estimatedDeploymentGas: gas.toString(), maxFeePerGasWei: fee.maxFeePerGas.toString(),
  estimatedExecutionFeeCeilingETH: formatEther(gas * fee.maxFeePerGas),
  checkpointGasCapEach: 200000, checkpointsPerPurchase: 2,
  note: "No token transfer. L1 data fees and changing base fees are additional; address changes if writer nonce changes. Separate approval required before deployment and checkpoint writes."
}, null, 2));
