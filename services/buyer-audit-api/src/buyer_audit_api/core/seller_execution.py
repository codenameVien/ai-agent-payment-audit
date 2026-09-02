from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SellerExecutionState(StrEnum):
    CLAIMED = "CLAIMED"
    SUBMITTED = "SUBMITTED"
    SETTLED = "SETTLED"
    PROVIDER_SUBMITTED = "PROVIDER_SUBMITTED"
    DELIVERED = "DELIVERED"


@dataclass(frozen=True, slots=True)
class SellerExecution:
    purchase_id: str
    quote_id: str
    seller_agent_id: str
    prompt_hash: str
    payment_proof_hash: str
    prompt_payload_id: str
    state: SellerExecutionState
    claimed_at: datetime
    authorization_payload_id: str | None = None
    settlement_payload_id: str | None = None
    provider_attempt_id: str | None = None
    provider_attempt_token: str | None = None
    result_payload_id: str | None = None
