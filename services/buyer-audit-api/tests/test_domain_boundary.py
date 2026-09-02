from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).parents[1] / "src" / "buyer_audit_api" / "core"
FORBIDDEN = ("domains.ai_inference", "gemini", "nemotron")


def test_core_does_not_import_ai_inference_implementation() -> None:
    violations: list[str] = []
    for source_path in CORE.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            for name in imported:
                if any(marker in name.lower() for marker in FORBIDDEN):
                    violations.append(f"{source_path.name}: {name}")
    assert not violations, violations


@pytest.mark.asyncio
async def test_fake_domain_creates_protocol_neutral_evidence(container, repository) -> None:
    created = await container.purchase_service.create(
        owner_address="0x0000000000000000000000000000000000000001",
        domain_id="fake",
        raw_request={"value": "  HELLO  ", "secret": "do-not-leak"},
        budget_units=10,
        policy={"mode": "test"},
    )

    events = await repository.list_events(created.purchase_id)
    sensitive = await repository.get_sensitive_payload(created.sensitive_payload_id)

    assert events[0].payload["normalizedRequest"] == {"normalized": "hello"}
    assert "do-not-leak" not in str(events[0].payload)
    assert sensitive is not None
    assert container.cipher.decrypt_json(sensitive)["secret"] == "do-not-leak"
