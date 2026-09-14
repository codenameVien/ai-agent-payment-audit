from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from buyer_audit_api.core.models import (
    BASE_SEPOLIA_CHAIN_ID,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
    ScenarioMetadata,
    TransactionRef,
)

EVM_TRANSACTION_HASH_REGEX = r"^0x[0-9a-f]{64}$"
LOCAL_TRANSACTION_ID_REGEX = r"^localtx:[0-9a-f]{32}:(payment|feedback):[0-9]{6}$"
SCENARIO_RUN_ID_REGEX = r"^[0-9a-f]{32}$"
SHA256_REGEX = r"^sha256:[0-9a-f]{64}$"


class ScenarioRefModel(BaseModel):
    """Attested synthetic-run provenance; never present on production evidence."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=SCENARIO_RUN_ID_REGEX)
    scenario_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    catalog_hash: str = Field(min_length=1)

    def to_core(self) -> ScenarioMetadata:
        return ScenarioMetadata(
            run_id=self.run_id,
            scenario_id=self.scenario_id,
            catalog_version=self.catalog_version,
            catalog_hash=self.catalog_hash,
        )


class ScenarioResponse(BaseModel):
    run_id: str
    scenario_id: str
    catalog_version: str


class EvmTransactionRefModel(BaseModel):
    """The EVM variant rejects `localtx:` identifiers and any extra local field."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["EVM"]
    hash: str = Field(pattern=EVM_TRANSACTION_HASH_REGEX)
    chain_id: int = BASE_SEPOLIA_CHAIN_ID
    evidence_source: Literal["BASE_SEPOLIA_VERIFIED", "HISTORICAL_ON_CHAIN"] = (
        "BASE_SEPOLIA_VERIFIED"
    )
    block_number: int | None = Field(default=None, ge=0)
    log_index: int | None = Field(default=None, ge=0)

    def to_core(self) -> EvmTransactionRef:
        return EvmTransactionRef(
            hash=self.hash,
            evidence_source=EvidenceSource(self.evidence_source),
            chain_id=self.chain_id,
            block_number=self.block_number,
            log_index=self.log_index,
        )


class LocalTransactionRefModel(BaseModel):
    """The local variant rejects `0x` hashes and can only be SYNTHETIC_LOCAL."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["LOCAL"]
    id: str = Field(pattern=LOCAL_TRANSACTION_ID_REGEX)
    run_id: str = Field(pattern=SCENARIO_RUN_ID_REGEX)
    evidence_source: Literal["SYNTHETIC_LOCAL"] = "SYNTHETIC_LOCAL"

    def to_core(self) -> LocalTransactionRef:
        return LocalTransactionRef(
            id=self.id,
            run_id=self.run_id,
            evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
        )


def transaction_ref_response(
    reference: TransactionRef | None,
) -> EvmTransactionRefModel | LocalTransactionRefModel | None:
    if reference is None:
        return None
    if isinstance(reference, EvmTransactionRef):
        return EvmTransactionRefModel(
            kind="EVM",
            hash=reference.hash,
            chain_id=reference.chain_id,
            evidence_source=(
                "HISTORICAL_ON_CHAIN"
                if reference.evidence_source is EvidenceSource.HISTORICAL_ON_CHAIN
                else "BASE_SEPOLIA_VERIFIED"
            ),
            block_number=reference.block_number,
            log_index=reference.log_index,
        )
    return LocalTransactionRefModel(
        kind="LOCAL",
        id=reference.id,
        run_id=reference.run_id,
    )


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


class CheckpointRecordResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    mode: Literal["mock", "live"]
    event_count: int = Field(alias="eventCount")
    head_event_hash: str = Field(alias="headEventHash")
    transaction_hash: str | None = Field(default=None, alias="transactionHash")
    chain_id: int | None = Field(default=None, alias="chainId")
    contract_address: str | None = Field(default=None, alias="contractAddress")


class EvidenceCheckpointResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    purchase_id: str = Field(alias="purchaseId")
    phase: Literal["decision", "audit"]
    event_count: int = Field(alias="eventCount")
    head_event_hash: str = Field(alias="headEventHash")
    record: CheckpointRecordResponse | None = None


class EvidenceCheckpointRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: Literal["mock", "live"]
    transaction_hash: str | None = Field(
        default=None, alias="transactionHash", pattern=EVM_TRANSACTION_HASH_REGEX
    )
    chain_id: int | None = Field(default=None, alias="chainId", ge=1)
    contract_address: str | None = Field(
        default=None, alias="contractAddress", pattern=r"^0x[0-9a-fA-F]{40}$"
    )
    event_count: int = Field(alias="eventCount", ge=1)
    head_event_hash: str = Field(alias="headEventHash", pattern=SHA256_REGEX)


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
    evidence_source: EvidenceSource | None = None
    scenario: ScenarioResponse | None = None
    transaction_ref: EvmTransactionRefModel | LocalTransactionRefModel | None = None


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
    transaction_hash: str | None = Field(default=None, pattern=EVM_TRANSACTION_HASH_REGEX)
    local_transaction_id: str | None = Field(default=None, pattern=LOCAL_TRANSACTION_ID_REGEX)
    scenario: ScenarioRefModel | None = None


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
    reconciliation_attempt_count: int = 0
    reconciliation_first_checked_at: datetime | None = None
    reconciliation_last_checked_at: datetime | None = None
    reconciliation_last_outcome: str | None = None
    actual_transfer: dict[str, Any] | None = None
    mismatched_fields: list[str] = Field(default_factory=list)
    terminal_outcome_key: str | None = None
    terminal_proof_ref: str | None = None
    terminal_evidence_source: EvidenceSource | None = None
    local_transaction_id: str | None = None
    no_transfer_reason_code: str | None = None


class AuditFindingResponse(BaseModel):
    code: str
    severity: str
    title: str
    detail: str
    evidence_refs: list[str]
    authority: str
    rule_id: str
    ruleset_version: str
    expected: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    mismatched_fields: list[str] = Field(default_factory=list)


class AuditReportResponse(BaseModel):
    report_id: str
    purchase_id: str
    severity: str
    findings: list[AuditFindingResponse]
    evidence_head_event_hash: str
    audit_bundle_hash: str
    ruleset_version: str


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
    audit_covers_head: bool = False
    lifecycle_status: str
    payment_status: str
    audit_status: str
    evidence_source: EvidenceSource | None = None
    transaction_ref: EvmTransactionRefModel | LocalTransactionRefModel | None = None
    scenario: ScenarioResponse | None = None
    mismatched_fields: list[str] = Field(default_factory=list)


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
    rule_id: str
    ruleset_version: str
    expected: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    mismatched_fields: list[str] = Field(default_factory=list)
    evidence_source: EvidenceSource | None = None
    scenario: ScenarioResponse | None = None


class AegisDecisionEvidenceResponse(BaseModel):
    """The fixed `aa-three-factor-v1` decision, served to the runtime by the Evidence API.

    The Provider Gateway and the payment execution module never reach MongoDB and never
    trust a client-supplied amount; they recompute from exactly this evidence.
    """

    model_config = ConfigDict(extra="forbid")

    purchase_id: str
    decision: JsonObject
    decision_event_hash: str = Field(pattern=SHA256_REGEX)
    snapshot: JsonObject
    snapshot_event_hash: str = Field(pattern=SHA256_REGEX)
    snapshot_hash: str = Field(pattern=SHA256_REGEX)


class AegisPaymentTermsResponse(BaseModel):
    """The immutable terms a 402 challenge and an ERC-3009 signature must reproduce."""

    model_config = ConfigDict(extra="forbid")

    purchase_id: str
    amount_units: int = Field(ge=0)
    budget_units: int = Field(ge=0)
    decision_event_hash: str = Field(pattern=SHA256_REGEX)
    snapshot_hash: str = Field(pattern=SHA256_REGEX)
    terms_binding_hash: str = Field(pattern=SHA256_REGEX)
    provider_id: str
    provider_model_id: str
    model_version: str
    recipient: str
    token: JsonObject


class AegisPaymentReservationResponse(BaseModel):
    """One durable attempt per purchase: the terms plus the intent that reserved it."""

    model_config = ConfigDict(extra="forbid")

    terms: AegisPaymentTermsResponse
    intent: PaymentIntentResponse


class InternalAegisSettlementRequest(BaseModel):
    """A Facilitator success answer. It is a status basis, never a chain verification."""

    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    facilitator_transaction: str = Field(min_length=1)
    facilitator_network: str = Field(min_length=1)
    facilitator_payer: str = Field(min_length=1)
    #: The amount the Facilitator itself reported, when it reported one.
    facilitator_amount: str | None = None


class InternalAegisFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class InternalAegisAmbiguousSettlementRequest(BaseModel):
    """A Facilitator success whose own fields contradict the terms it answers.

    The answer is carried verbatim, including the fields it omitted, so the stored
    evidence shows what arrived rather than what was expected.
    """

    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1)
    mismatch_reason: str = Field(min_length=1)
    success: bool
    network: str = Field(min_length=1)
    transaction: str = Field(min_length=1)
    payer: str | None = None
    amount: str | None = None


class InternalAegisDeliveryRequest(BaseModel):
    """The delivered Mock provider result, checked against the selected model."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    provider_model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    response_hash: str = Field(pattern=SHA256_REGEX)
    observed_execution_ms: int = Field(ge=0)
