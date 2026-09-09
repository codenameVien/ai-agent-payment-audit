#!/usr/bin/env bash
# Stores one user-owned Base Sepolia wallet key locally without sending it through chat.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_dir/.env.local"
example_file="$repo_dir/.env.example"

[[ -f "$env_file" ]] || cp "$example_file" "$env_file"

# This is the public wallet the user approved for this one deployment path. Older
# .env.local files predate the variable, so add only the public address if absent.
if ! grep -q '^PBLC_USER_ADDRESS=' "$env_file"; then
  printf '\nPBLC_USER_ADDRESS=0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3\nPBLC_USER_PRIVATE_KEY=\n' >> "$env_file"
fi

read -r -s -p "MetaMask private key for PBLC_USER_ADDRESS: " private_key
printf '\n'
[[ -n "$private_key" ]] || { printf 'No key entered; .env.local was not changed.\n' >&2; exit 1; }

PRIVATE_KEY="$private_key" ENV_FILE="$env_file" REPO_DIR="$repo_dir" node --input-type=module <<'NODE'
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { privateKeyToAccount } from "viem/accounts";

const envPath = process.env.ENV_FILE;
const repoDir = process.env.REPO_DIR;
const privateKey = process.env.PRIVATE_KEY?.trim();
if (!envPath || !repoDir || !privateKey) throw new Error("local input missing");
const account = privateKeyToAccount(privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`);
let text = await readFile(envPath, "utf8");
const addressLine = /^PBLC_USER_ADDRESS=(.+)$/m.exec(text);
const expected = addressLine?.[1]?.trim();
if (!expected) throw new Error("PBLC_USER_ADDRESS is required in .env.local");
if (account.address.toLowerCase() !== expected.toLowerCase()) {
  throw new Error(`entered key address ${account.address} does not match PBLC_USER_ADDRESS`);
}
const value = privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`;
if (/^PBLC_USER_PRIVATE_KEY=/m.test(text)) {
  text = text.replace(/^PBLC_USER_PRIVATE_KEY=.*$/m, `PBLC_USER_PRIVATE_KEY=${value}`);
} else {
  text = `${text.trimEnd()}\nPBLC_USER_PRIVATE_KEY=${value}\n`;
}
await writeFile(resolve(repoDir, ".env.local"), text, { mode: 0o600 });
console.log(`Stored local key for ${account.address}; the key value was not printed.`);
NODE
