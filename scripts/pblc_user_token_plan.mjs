#!/usr/bin/env node

/**
 * User-controlled PBLC deployment planner — PLAN ONLY.
 *
 * The deployed PBLC V2 owner is not a user wallet, so this computes a separate,
 * user-owned PBLC deployment packet.  It imports the audited offline planner only;
 * this file cannot sign, broadcast, read environment variables, or contact an RPC.
 */

import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildPlan, loadPlanInputs, parseArgs } from "./aegis_token_plan.mjs";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_PATH = resolve(REPO_ROOT, "infra/contracts/src/DemoTokenV2.sol");
const ARTIFACT_PATH = resolve(REPO_ROOT, "infra/contracts/out/DemoTokenV2.sol/DemoTokenV2.json");

export const USAGE = `Usage (plan only, offline):
  node scripts/pblc_user_token_plan.mjs \\
    --deployer <user public 0x address> \\
    --holder <buyer public 0x address> \\
    --nonce <deployer account nonce at send time> \\
    --initial-supply-units <integer, 6 decimals>

All common planner options from scripts/aegis_token_plan.mjs are supported. This command
never deploys, signs, reads .env files, or contacts a network.`;

export async function plan(argv) {
  const options = parseArgs(argv);
  const { source, artifact } = await loadPlanInputs({ sourcePath: SOURCE_PATH, artifactPath: ARTIFACT_PATH });
  return buildPlan({
    artifact,
    source,
    options,
    sourcePath: SOURCE_PATH,
    artifactPath: ARTIFACT_PATH,
    historicalAssetsNote:
      "Existing PBLC V1/V2 contracts and historical transactions are untouched; this is a separate user-owned PBLC deployment at a new address.",
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.stdout.write(`${JSON.stringify(await plan(process.argv.slice(2)), null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
