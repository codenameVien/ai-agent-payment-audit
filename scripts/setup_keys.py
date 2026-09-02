#!/usr/bin/env python3
"""Create local internal security keys without revealing them to chat or stdout."""

from __future__ import annotations

import base64
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env.local"
EXAMPLE_FILE = REPO_ROOT / ".env.example"
KEY_NAMES = (
    "SESSION_SECRET_BASE64",
    "PAYLOAD_MASTER_KEY_BASE64",
    "INTERNAL_SERVICE_TOKEN",
    "GATEWAY_SERVICE_TOKEN",
)
PRIVATE_KEY_NAMES = (
    "DEPLOYER_PRIVATE_KEY",
    "GEMINI_SELLER_PRIVATE_KEY",
    "NEMOTRON_SELLER_PRIVATE_KEY",
    "BUYER_AGENT_PRIVATE_KEY",
)
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", maxsplit=1)
        values[name] = value
    return values


def replace_value(text: str, name: str, value: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{name}="):
            lines[index] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    if not EXAMPLE_FILE.exists():
        raise SystemExit(f"missing template: {EXAMPLE_FILE}")
    if ENV_FILE.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = ENV_FILE.with_name(f"{ENV_FILE.name}.bak-{stamp}")
        shutil.copy2(ENV_FILE, backup)
        text = ENV_FILE.read_text(encoding="utf-8")
    else:
        text = EXAMPLE_FILE.read_text(encoding="utf-8")

    existing = parse_env(text)
    generated: list[str] = []
    for name in KEY_NAMES:
        if existing.get(name):
            continue
        value = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        text = replace_value(text, name, value)
        generated.append(name)

    for name in PRIVATE_KEY_NAMES:
        if existing.get(name):
            continue
        private_value = secrets.randbelow(SECP256K1_ORDER - 1) + 1
        value = f"0x{private_value:064x}"
        text = replace_value(text, name, value)
        generated.append(name)

    ENV_FILE.write_text(text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    if generated:
        print(f"생성 완료: {ENV_FILE} ({', '.join(generated)})")
    else:
        print(f"기존 키 유지: {ENV_FILE}")
    print("키 값은 출력하지 않았습니다.")


if __name__ == "__main__":
    main()
