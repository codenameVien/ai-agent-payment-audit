"""Ports the `aa-three-factor-v1` decision path depends on.

Keeping these here means the buyer workflow never learns whether the Artificial Analysis
numbers arrived from a fixture file or an authenticated HTTP call; it only records which
one it was, and refuses to mix a fixture capture with a live-mapped catalog.
"""

from __future__ import annotations

from typing import Protocol

from buyer_audit_api.core.models import ImmutableDocument
from buyer_audit_api.domains.ai_inference.aa_catalog import AaFieldPaths
from buyer_audit_api.domains.ai_inference.aa_models import AaSourceMode


class AaCaptureSource(Protocol):
    """One complete, ordered capture of the AA free language-model list."""

    @property
    def mode(self) -> AaSourceMode: ...

    @property
    def source_url(self) -> str: ...

    @property
    def field_paths(self) -> AaFieldPaths: ...

    async def fetch_pages(self) -> tuple[str, ...]:
        """Every received page body, in order, from the first to the last one."""
        ...


class ImmutableDocumentStore(Protocol):
    """The Evidence API side of immutable snapshot storage."""

    async def put_document(self, document: ImmutableDocument) -> ImmutableDocument: ...

    async def get_document(self, document_id: str) -> ImmutableDocument | None: ...


class DecisionExplanationPort(Protocol):
    """An LLM may restate the deterministic explanation; it can never change a number."""

    async def explain(self, *, deterministic_explanation: str) -> str: ...
