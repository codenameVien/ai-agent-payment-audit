#!/usr/bin/env bash
# Local-only helper. Run this yourself in a terminal; never paste the key into chat.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_dir/.env.local"
example_file="$repo_dir/.env.example"

if [[ ! -f "$env_file" ]]; then
  cp "$example_file" "$env_file"
fi

read -r -s -p "Artificial Analysis API key: " aa_api_key
printf '\n'
if [[ -z "$aa_api_key" ]]; then
  printf 'No key entered; .env.local was not changed.\n' >&2
  exit 1
fi

tmp_file="$(mktemp "${env_file}.tmp.XXXXXX")"
trap 'rm -f "$tmp_file"' EXIT
awk -v value="$aa_api_key" '
  /^AA_API_KEY=/ { print "AA_API_KEY=" value; found = 1; next }
  { print }
  END { if (!found) print "AA_API_KEY=" value }
' "$env_file" > "$tmp_file"
mv "$tmp_file" "$env_file"
trap - EXIT

printf 'AA_API_KEY was stored in .env.local. It remains gitignored.\n'
printf 'Next: configure AA_MODEL_CATALOG_PATH with exact ids/slugs before starting live AA.\n'
