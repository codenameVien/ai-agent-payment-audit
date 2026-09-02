#!/usr/bin/env python3
"""Collect external provider keys locally without echoing or logging values."""

from __future__ import annotations

import getpass
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env.local"
EXAMPLE_FILE = REPO_ROOT / ".env.example"
PROVIDER_KEYS = (
    ("GEMINI_API_KEY", "Gemini API key"),
    ("NVIDIA_API_KEY", "NVIDIA/Nemotron API key"),
)


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

    updated: list[str] = []
    for name, label in PROVIDER_KEYS:
        value = getpass.getpass(f"{label} (Enter로 건너뜀): ").strip()
        if not value:
            continue
        text = replace_value(text, name, value)
        updated.append(name)

    ENV_FILE.write_text(text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    if updated:
        print(f"저장 완료: {ENV_FILE} ({', '.join(updated)})")
    else:
        print("입력된 키가 없어 기존 값을 유지했습니다.")
    print("키 값은 출력하지 않았습니다.")


if __name__ == "__main__":
    main()
