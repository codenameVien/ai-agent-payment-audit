"""Original request payloads kept outside the evidence chain.

The chain records only the content hash of the original request. The request itself is
stored in its own collection, in plain JSON, so it can be read back and checked against
that hash. It is not encrypted: this is a single-user local demo whose database and
configuration live on the same machine.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.hashing import canonical_bytes, sha256_bytes
from buyer_audit_api.core.models import JsonObject, SensitivePayload


def new_sensitive_payload(
    *,
    purchase_id: str,
    kind: str,
    value: Mapping[str, Any],
    created_at: datetime,
) -> SensitivePayload:
    body: JsonObject = dict(value)
    return SensitivePayload(
        payload_id=str(uuid.uuid4()),
        purchase_id=purchase_id,
        kind=kind,
        value=body,
        content_hash=sha256_bytes(canonical_bytes(body)),
        created_at=created_at,
    )


def read_sensitive_payload(payload: SensitivePayload) -> JsonObject:
    """Return the stored value only if it still matches the hash recorded with it."""
    if sha256_bytes(canonical_bytes(payload.value)) != payload.content_hash:
        raise EvidenceIntegrityError("sensitive payload content hash mismatch")
    return payload.value
