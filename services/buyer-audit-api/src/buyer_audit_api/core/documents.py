"""Content-addressed immutable side documents for the append-only evidence store.

A purchase event references a document by id; the document body is hashed together with
its purchase and kind, so the id cannot be reused for different content and a repeated
store of the identical body is idempotent rather than an overwrite.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from buyer_audit_api.core.errors import EvidenceImmutabilityError
from buyer_audit_api.core.hashing import sha256_bytes, sha256_json
from buyer_audit_api.core.models import ImmutableDocument, JsonObject

DOCUMENT_HASH_ALGORITHM = "sha256"
DOCUMENT_HASH_ENCODING = "rfc8785-canonical-json"
_DOCUMENT_ID_DIGEST_LENGTH = 32


def raw_parts_hash(parts: Sequence[str]) -> str:
    """Hash received bodies as bytes, length-prefixed so their order is unambiguous.

    Canonical JSON is not used for captured external responses: re-serializing them
    could not represent the published literals faithfully, and the point of this hash is
    to bind the bytes that actually arrived.
    """
    buffer = bytearray()
    for part in parts:
        encoded = part.encode("utf-8")
        buffer.extend(f"{len(encoded)}\n".encode("ascii"))
        buffer.extend(encoded)
    return sha256_bytes(bytes(buffer))


def document_content_hash(*, purchase_id: str, kind: str, payload: JsonObject) -> str:
    """Bind the body to the purchase and kind that may reference it."""
    return sha256_json({"kind": kind, "payload": payload, "purchaseId": purchase_id})


def build_immutable_document(
    *,
    purchase_id: str,
    kind: str,
    payload: JsonObject,
    created_at: datetime,
) -> ImmutableDocument:
    if not purchase_id.strip():
        raise ValueError("an immutable document requires its purchaseId")
    if not kind.strip():
        raise ValueError("an immutable document requires its kind")
    content_hash = document_content_hash(
        purchase_id=purchase_id, kind=kind, payload=payload
    )
    digest = content_hash.split(":", 1)[1][:_DOCUMENT_ID_DIGEST_LENGTH]
    return ImmutableDocument(
        document_id=f"{kind}:{digest}",
        purchase_id=purchase_id,
        kind=kind,
        content_hash=content_hash,
        created_at=created_at,
        payload=payload,
    )


def verify_immutable_document(document: ImmutableDocument) -> ImmutableDocument:
    """Reject a document whose declared hash does not describe its own body."""
    expected = document_content_hash(
        purchase_id=document.purchase_id,
        kind=document.kind,
        payload=document.payload,
    )
    if document.content_hash != expected:
        raise EvidenceImmutabilityError("immutable document content hash mismatch")
    digest = expected.split(":", 1)[1][:_DOCUMENT_ID_DIGEST_LENGTH]
    if document.document_id != f"{document.kind}:{digest}":
        raise EvidenceImmutabilityError("immutable document id is not content addressed")
    return document
