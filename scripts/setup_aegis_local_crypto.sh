#!/usr/bin/env bash
# Create missing local-only API secrets without ever printing them.
# Existing values are preserved so this never rotates a key that may decrypt evidence.
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
env_file="$repo_root/.env.local"
[[ -f "$env_file" ]] || { echo "missing: $env_file" >&2; exit 1; }

ENV_FILE="$env_file" node <<'NODE'
const crypto = require("node:crypto");
const fs = require("node:fs");

const path = process.env.ENV_FILE;
let text = fs.readFileSync(path, "utf8");
const created = [];
for (const name of ["SESSION_SECRET_BASE64", "PAYLOAD_MASTER_KEY_BASE64"]) {
  const expression = new RegExp(`^${name}=(.*)$`, "m");
  const current = expression.exec(text)?.[1]?.trim() ?? "";
  if (current) continue;
  const value = crypto.randomBytes(32).toString("base64");
  const line = `${name}=${value}`;
  text = expression.test(text) ? text.replace(expression, line) : `${text.trimEnd()}\n${line}\n`;
  created.push(name);
}
fs.writeFileSync(path, text, { encoding: "utf8", mode: 0o600 });
process.stdout.write(created.length
  ? `Created local-only encryption/session keys: ${created.join(", ")}\n`
  : "Local encryption/session keys already configured; no values changed.\n");
NODE
