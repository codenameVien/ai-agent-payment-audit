from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PriorityPreset(StrEnum):
    BALANCED = "balanced"
    QUALITY = "quality"
    PRICE = "price"
    SPEED = "speed"


class NormalizedAiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_length: int = Field(ge=1, le=20_000)
    priority: PriorityPreset = PriorityPreset.BALANCED
    required_capabilities: tuple[str, ...] = ()
    allowed_sellers: tuple[str, ...] = ()
    min_input_limit: int = Field(default=1, ge=1)
    min_output_limit: int = Field(default=1, ge=1)
    max_latency_ms: int | None = Field(default=None, gt=0)


class BenchmarkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    provider_id: str
    model_id: str
    model_version: str
    observed_at: datetime
    source_url: str
    content_hash: str
    quality_score: float = Field(ge=0, le=100)
    speed_score: float = Field(ge=0, le=100)
    reputation_score: float = Field(ge=0, le=100)
    capabilities: tuple[str, ...] = ()


class SellerQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str
    purchase_id: str
    seller_agent_id: str
    erc8004_agent_id: str = Field(pattern=r"^[0-9]+$")
    provider_id: str
    model_id: str
    model_version: str
    amount_units: int = Field(ge=0)
    token: str
    pay_to: str
    expected_latency_ms: int = Field(gt=0)
    input_limit: int = Field(gt=0)
    output_limit: int = Field(gt=0)
    available: bool
    identity_verified: bool
    expires_at: datetime
    quote_nonce: str
    counteroffer_of: str | None = None
    counteroffer_reason: str | None = None
    signature: str = Field(min_length=3)
    signer_address: str = Field(min_length=3)
    chain_id: int = Field(gt=0)
    verifying_contract: str = Field(min_length=3)


class SellerIdentityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    quote_id: str = Field(alias="quoteId")
    erc8004_agent_id: str = Field(alias="erc8004AgentId", pattern=r"^[0-9]+$")
    identity_registry: str = Field(alias="identityRegistry")
    agent_wallet: str = Field(alias="agentWallet")
    signer_address: str = Field(alias="signerAddress")
    identity_verified: bool = Field(alias="identityVerified")


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    questions: tuple[str, ...] = ()


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: SellerQuote
    benchmark: BenchmarkSnapshot


class ScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str
    provider_id: str
    model_id: str
    total_score: float
    component_scores: dict[str, float]
    weights: dict[str, int]


class RejectedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str
    provider_id: str
    model_id: str
    reasons: tuple[str, ...]


class SelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: PriorityPreset
    winner: ScoredCandidate
    eligible: tuple[ScoredCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]
    benchmark_snapshot_ids: tuple[str, ...]
    explanation: str
