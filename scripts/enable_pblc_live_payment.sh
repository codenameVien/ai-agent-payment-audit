#!/usr/bin/env bash
# Enable the local real-PBLC runtime without handling or printing any secret.
# A transaction is still impossible until the user submits one consented `/request`.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
env_file="$repo_root/.env.local"
[[ -f "$env_file" ]] || { echo "missing: $env_file" >&2; exit 1; }

read -r -p "Type ENABLE_LIVE_PBLC_PAYMENT to enable the live local runtime: " confirmation
[[ "$confirmation" == "ENABLE_LIVE_PBLC_PAYMENT" ]] || {
  echo "No changes made."
  exit 1
}

ENV_FILE="$env_file" node <<'NODE'
const fs = require("node:fs");
const path = process.env.ENV_FILE;
let text = fs.readFileSync(path, "utf8");
for (const [name, value] of Object.entries({
  AEGIS_EXECUTION_MODE: "live",
  AEGIS_REAL_PAYMENT_APPROVED: "yes",
  AEGIS_FACILITATOR_URL: "https://x402.org/facilitator",
})) {
  const expression = new RegExp(`^${name}=.*$`, "m");
  const line = `${name}=${value}`;
  text = expression.test(text) ? text.replace(expression, line) : `${text.trimEnd()}\n${line}\n`;
}
fs.writeFileSync(path, text, "utf8");
NODE

echo "Live PBLC runtime enabled in .env.local. No payment was signed or broadcast."
