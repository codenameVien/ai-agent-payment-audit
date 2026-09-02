from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    NormalizedAiRequest,
)


class ManualBenchmarkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    observed_at: datetime
    source_url: str = Field(min_length=1)
    quality_score: float = Field(ge=0, le=100)
    speed_score: float = Field(ge=0, le=100)
    reputation_score: float = Field(ge=0, le=100)
    capabilities: tuple[str, ...] = ()


def normalize_manual_benchmark(payload: Mapping[str, Any]) -> BenchmarkSnapshot:
    record = ManualBenchmarkRecord.model_validate(payload)
    canonical = record.model_dump(mode="json")
    content_hash = sha256_json(canonical)
    return BenchmarkSnapshot(
        snapshot_id=f"manual-{content_hash[:20]}",
        provider_id=record.provider_id.strip().lower(),
        model_id=record.model_id.strip(),
        model_version=record.model_version.strip(),
        observed_at=record.observed_at,
        source_url=record.source_url,
        content_hash=content_hash,
        quality_score=record.quality_score,
        speed_score=record.speed_score,
        reputation_score=record.reputation_score,
        capabilities=tuple(
            sorted(
                {
                    capability.strip().lower()
                    for capability in record.capabilities
                    if capability.strip()
                }
            )
        ),
    )


class ManualBenchmarkProvider:
    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        self._snapshots = tuple(normalize_manual_benchmark(record) for record in records)

    async def candidates(self, request: NormalizedAiRequest) -> list[BenchmarkSnapshot]:
        snapshots = self._snapshots
        if request.allowed_sellers:
            snapshots = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.provider_id in request.allowed_sellers
            )
        return sorted(
            snapshots, key=lambda item: (item.provider_id, item.model_id, item.snapshot_id)
        )
