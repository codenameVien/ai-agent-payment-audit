from __future__ import annotations

from typing import Protocol

from buyer_audit_api.core.reputation import ReputationSnapshot
from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    NormalizedAiRequest,
    SellerIdentityEvidence,
    SellerQuote,
)


class BenchmarkProvider(Protocol):
    async def candidates(self, request: NormalizedAiRequest) -> list[BenchmarkSnapshot]: ...


class SellerQuoteClient(Protocol):
    async def quote(
        self,
        *,
        purchase_id: str,
        request: NormalizedAiRequest,
        benchmark: BenchmarkSnapshot,
    ) -> SellerQuote: ...


class SellerQuoteVerifier(Protocol):
    def verify(self, quote: SellerQuote) -> bool: ...


class SellerIdentityVerifier(Protocol):
    async def verify(self, quote: SellerQuote) -> SellerIdentityEvidence: ...


class SelectionExplanationPort(Protocol):
    async def explain(self, *, deterministic_explanation: str) -> str: ...


class SellerReputationProvider(Protocol):
    """`P6-AC-06.4`: the provider-level ERC-8004 reputation query.

    `None` means no provider is configured or the query could not be answered. It is
    handled as an explicit neutral, never as a favourable score.
    """

    async def snapshot(
        self, *, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot | None: ...
