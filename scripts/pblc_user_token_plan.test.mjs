import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { plan } from "./pblc_user_token_plan.mjs";

const DEPLOYER = "0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3";
const ARGS = [
  "--deployer", DEPLOYER,
  "--holder", DEPLOYER,
  "--nonce", "0",
  "--initial-supply-units", "1000000000000",
  "--chain-id", "84532",
];

test("user-owned PBLC plan is public-input-only and keeps the old contract distinct", async () => {
  const source = await readFile(new URL("./pblc_user_token_plan.mjs", import.meta.url), "utf8");
  for (const forbidden of ["process.env", "fetch(", "createPublicClient", "createWalletClient", "deployContract", "privateKeyToAccount"]) {
    assert.equal(source.includes(forbidden), false, `planner must not reference ${forbidden}`);
  }
  const output = await plan(ARGS);
  assert.equal(output.action, "PLAN_ONLY_NO_TRANSACTION_SENT");
  assert.deepEqual(
    { name: output.token.name, symbol: output.token.symbol, eip712DomainVersion: output.token.eip712DomainVersion, decimals: output.token.decimals },
    { name: "PBL Agent Credit", symbol: "PBLC", eip712DomainVersion: "2", decimals: 6 },
  );
  assert.equal(output.deployment.initialOwner, DEPLOYER);
  assert.equal(output.deployment.initialHolder, DEPLOYER);
  assert.equal(output.token.source, "infra/contracts/src/DemoTokenV2.sol");
  assert.equal(output.artifact.path, "infra/contracts/out/DemoTokenV2.sol/DemoTokenV2.json");
  assert.match(output.notes.join("\n"), /separate user-owned PBLC deployment/);
  assert.equal(output.approval.status, "PENDING_USER_APPROVAL");
});
