from __future__ import annotations

from typing import Protocol

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
