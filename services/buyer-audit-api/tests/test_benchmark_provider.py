from __future__ import annotations

from datetime import UTC, datetime

import pytest

from buyer_audit_api.domains.ai_inference.benchmark import (
    ManualBenchmarkProvider,
    normalize_manual_benchmark,
)
from buyer_audit_api.domains.ai_inference.models import NormalizedAiRequest


def record(provider: str = "Gemini") -> dict:
    return {
        "provider_id": provider,
        "model_id": f"{provider.lower()}-model",
        "model_version": "v1",
        "observed_at": datetime(2026, 9, 2, tzinfo=UTC),
        "source_url": "https://example.test/manual-benchmark",
        "quality_score": 90,
        "speed_score": 80,
        "reputation_score": 85,
        "capabilities": ["Korean", "json", "korean"],
    }


def test_manual_snapshot_normalization_is_deterministic() -> None:
    first = normalize_manual_benchmark(record())
    second = normalize_manual_benchmark(record())
    assert first == second
    assert first.provider_id == "gemini"
    assert first.capabilities == ("json", "korean")
    assert first.snapshot_id.startswith("manual-")


@pytest.mark.asyncio
async def test_provider_filters_allowed_sellers_and_sorts() -> None:
    provider = ManualBenchmarkProvider([record("Nemotron"), record("Gemini")])
    snapshots = await provider.candidates(
        NormalizedAiRequest(
            prompt_hash="sha256:" + "0" * 64,
            prompt_length=5,
            allowed_sellers=("gemini",),
        )
    )
    assert [snapshot.provider_id for snapshot in snapshots] == ["gemini"]
