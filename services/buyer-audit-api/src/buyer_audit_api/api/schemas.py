from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChallengeRequest(BaseModel):
    owner_address: str


class ChallengeResponse(BaseModel):
    owner_address: str
    nonce: str
    message: str
    expires_at: datetime


class VerifyRequest(BaseModel):
    message: str
    signature: str


class MeResponse(BaseModel):
    owner_address: str
    buyer_wallet_address: str | None


class BuyerWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_address: str
    buyer_wallet_address: str


class BuyerWalletResponse(BaseModel):
    owner_address: str
    buyer_wallet_address: str


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    request: dict[str, Any]
    budget_units: int | None = Field(default=None, ge=0)
    policy: dict[str, Any] = Field(default_factory=dict)


class PurchaseResponse(BaseModel):
    purchase_id: str
    event_hash: str
    sensitive_payload_id: str


class PurchaseRunResponse(BaseModel):
    purchase_id: str
    payment: dict[str, Any]
    audit: AuditReportResponse


class SellerQuoteTermsResponse(BaseModel):
    purchase_id: str
    quote_id: str
    model_id: str
    amount_units: int
    token: str
    pay_to: str
    expires_at: datetime


class SellerExecutionClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    seller_agent_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payment_proof_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SellerExecutionValueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    value: dict[str, Any]


class SellerExecutionProviderSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    provider_attempt_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_attempt_token: str = Field(min_length=32, max_length=128)


class SellerExecutionResponse(BaseModel):
    purchase_id: str
    quote_id: str
    seller_agent_id: str
    prompt: str
    prompt_hash: str
    payment_proof_hash: str
    state: str
    authorization: dict[str, Any] | None
    settlement: dict[str, Any] | None
    provider_attempt_id: str | None
    provider_attempt_token: str | None
    result: dict[str, Any] | None


class EventResponse(BaseModel):
    event_id: str
    purchase_id: str
    sequence: int
    type: str
    occurred_at: datetime
    actor: dict[str, Any]
    payload: dict[str, Any]
    payload_hash: str
    previous_event_hash: str | None
    event_hash: str
    evidence_refs: list[str]
    redacted: bool = False


class SensitivePayloadResponse(BaseModel):
    purchase_id: str
    payload_id: str
    kind: str
    content_hash: str
    value: dict[str, Any]


class WalletPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_wallet_address: str
    policy_date: str
    token: str
    per_transaction_limit_units: int = Field(ge=0)
    daily_limit_units: int = Field(ge=0)


class WalletPolicyResponse(WalletPolicyRequest):
    spent_units: int
    reserved_units: int


class InternalPaymentClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)


class InternalPaymentAuthorizeRequest(InternalPaymentClaimRequest):
    authorization_hash: str = Field(min_length=1)
    signature: str = Field(min_length=1)


class InternalPaymentReconciliationRequest(InternalPaymentClaimRequest):
    reason: str = Field(min_length=1)
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-fA-F]{64}$")


class InternalPaymentTransactionBindingRequest(InternalPaymentClaimRequest):
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")


class InternalPaymentSettlementRequest(InternalPaymentClaimRequest):
    transaction_hash: str
    block_number: int = Field(ge=0)
    transfer_log_index: int = Field(ge=0)
    receipt_status: int
    token: str
    from_address: str
    to_address: str
    amount_units: int = Field(ge=0)


class InternalPaymentFailureRequest(InternalPaymentClaimRequest):
    reason: str = Field(min_length=1)
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    block_number: int = Field(ge=0)
    receipt_status: int


class PaymentQuoteViewResponse(BaseModel):
    quote_id: str
    seller_agent_id: str
    erc8004_agent_id: str
    provider_id: str
    model_id: str
    model_version: str
    amount_units: int
    token: str
    pay_to: str
    expires_at: datetime
    signer_address: str
    chain_id: int
    verifying_contract: str


class PaymentViewResponse(BaseModel):
    purchase_id: str
    owner_address: str
    buyer_wallet_address: str
    budget_units: int
    request_policy: dict[str, Any]
    decision_event_hash: str
    quote: PaymentQuoteViewResponse
    event_count: int
    head_event_hash: str


class PaymentIntentResponse(BaseModel):
    purchase_id: str
    buyer_wallet_address: str
    policy_date: str
    quote_id: str
    decision_event_hash: str
    amount_units: int
    token: str
    pay_to: str
    permit2_nonce: str | None
    transfer_method: str
    authorization_nonce: str | None
    state: str
    claimed_at: datetime
    decision_authorization_hash: str | None
    decision_authorization_signature: str | None
    authorized_at: datetime | None
    reconciliation_reason: str | None
    transaction_hash: str | None
    block_number: int | None
    transfer_log_index: int | None
    settlement_verified_at: datetime | None
    failure_reason: str | None
    failed_at: datetime | None


class EvidenceHeadResponse(BaseModel):
    purchase_id: str
    event_count: int
    head_event_hash: str


class ExternalAnchorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchored_event_count: int = Field(ge=1)
    anchored_head_event_hash: str = Field(min_length=1)
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    chain_id: int = Field(ge=1)
    contract_address: str = Field(min_length=1)


class DeliveryRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_agent_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    response_hash: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class DeliveryStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_agent_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DeliveryStageResponse(DeliveryStageRequest):
    response_hash: str


class ReputationRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    erc8004_agent_id: str = Field(pattern=r"^[0-9]+$")
    objective_value: int
    feedback_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    chain_id: int = Field(ge=1)
    registry_address: str = Field(min_length=1)


class ReputationIntentResponse(BaseModel):
    erc8004_agent_id: str
    objective_value: int
    feedback_hash: str
    transaction_hash: str | None = None


class AuditFindingResponse(BaseModel):
    code: str
    severity: str
    title: str
    detail: str
    evidence_refs: list[str]
    authority: str


class AuditReportResponse(BaseModel):
    report_id: str
    purchase_id: str
    severity: str
    findings: list[AuditFindingResponse]
    evidence_head_event_hash: str
    audit_bundle_hash: str


class WalletDashboardResponse(BaseModel):
    owner_address: str
    buyer_wallet_address: str | None
    token: str | None
    token_balance_units: int | None
    balance_status: str
    policy_date: str | None
    daily_limit_units: int | None
    spent_units: int
    reserved_units: int


class PurchaseSummaryResponse(BaseModel):
    purchase_id: str
    created_at: datetime
    domain: str
    request_summary: dict[str, Any]
    status: str
    amount_units: int | None
    token: str | None
    transaction_hash: str | None
    audit_severity: str | None
    finding_count: int


class PurchaseDetailResponse(BaseModel):
    summary: PurchaseSummaryResponse
    events: list[EventResponse]
    audit: AuditReportResponse | None


class AuditAlertResponse(BaseModel):
    purchase_id: str
    report_id: str
    code: str
    severity: str
    title: str
    detail: str
    evidence_refs: list[str]
    authority: str


class SellerAgentSummaryResponse(BaseModel):
    seller_agent_id: str
    erc8004_agent_id: str | None
    signer_address: str | None
    identity_verified: bool
    quote_count: int
    reputation_status: str
    objective_feedback_value: int | None
    reputation_transaction_hash: str | None
