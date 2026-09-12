from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

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
from buyer_audit_api.core.payment import (
    ActualTransfer,
    ConfirmedMismatchProof,
    NoTransferProof,
    PaymentIntentState,
    ReconciliationCheck,
    ReconciliationVerifierOutcome,
)
from buyer_audit_api.core.reputation import ConfirmedFeedbackProof

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


TransactionRefModel = Annotated[
    EvmTransactionRefModel | LocalTransactionRefModel,
    Field(discriminator="kind"),
]


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


class InternalPaymentTransactionBindingRequest(InternalPaymentClaimRequest):
    transaction_hash: str = Field(pattern=EVM_TRANSACTION_HASH_REGEX)


class InternalPaymentSettlementRequest(InternalPaymentClaimRequest):
    transaction_hash: str = Field(pattern=EVM_TRANSACTION_HASH_REGEX)
    block_number: int = Field(ge=0)
    transfer_log_index: int = Field(ge=0)
    receipt_status: int
    token: str
    from_address: str
    to_address: str
    amount_units: int = Field(ge=0)


class InternalPaymentFailureRequest(InternalPaymentClaimRequest):
    reason: str = Field(min_length=1)
    transaction_hash: str = Field(pattern=EVM_TRANSACTION_HASH_REGEX)
    block_number: int = Field(ge=0)
    receipt_status: int


class InternalTerminalProofRequest(InternalPaymentClaimRequest):
    """Design 18.12.2: every terminal mutation states the state and head it observed."""

    expected_state: PaymentIntentState
    expected_event_count: int = Field(ge=1)
    expected_head_event_hash: str = Field(pattern=SHA256_REGEX)


class InternalReconciliationCheckRequest(InternalTerminalProofRequest):
    attempt_number: int = Field(ge=1)
    checked_at: datetime
    checked_chain_id: int = Field(ge=1)
    submission_ref: str = Field(min_length=1)
    verifier_outcome: ReconciliationVerifierOutcome
    finality_confirmations: int = Field(ge=0)
    proof_ref: str = Field(min_length=1)
    evidence_source: EvidenceSource
    receipt_status: int | None = Field(default=None, ge=0, le=1)
    block_number: int | None = Field(default=None, ge=0)
    authorization_state: str | None = None
    scenario: ScenarioRefModel | None = None

    def to_core(self) -> ReconciliationCheck:
        return ReconciliationCheck(
            attempt_number=self.attempt_number,
            checked_at=self.checked_at,
            checked_chain_id=self.checked_chain_id,
            submission_ref=self.submission_ref,
            verifier_outcome=self.verifier_outcome,
            finality_confirmations=self.finality_confirmations,
            proof_ref=self.proof_ref,
            evidence_source=self.evidence_source,
            receipt_status=self.receipt_status,
            block_number=self.block_number,
            authorization_state=self.authorization_state,
            scenario=self.scenario.to_core() if self.scenario is not None else None,
        )


class InternalConfirmMismatchRequest(InternalTerminalProofRequest):
    actual_amount_units: int = Field(gt=0)
    actual_token: str = Field(min_length=1)
    actual_from: str = Field(min_length=1)
    actual_to: str = Field(min_length=1)
    transaction_ref: TransactionRefModel
    proof_ref: str = Field(min_length=1)
    evidence_source: EvidenceSource
    scenario: ScenarioRefModel | None = None

    def to_core(self) -> ConfirmedMismatchProof:
        return ConfirmedMismatchProof(
            actual_transfer=ActualTransfer(
                amount_units=self.actual_amount_units,
                token=self.actual_token,
                from_address=self.actual_from,
                to_address=self.actual_to,
            ),
            transaction_ref=self.transaction_ref.to_core(),
            proof_ref=self.proof_ref,
            evidence_source=self.evidence_source,
            scenario=self.scenario.to_core() if self.scenario is not None else None,
        )


class InternalReconcileNoTransferRequest(InternalTerminalProofRequest):
    reason_code: str = Field(min_length=1)
    checked_chain_id: int = Field(ge=1)
    attempt_count: int = Field(ge=1)
    first_checked_at: datetime
    last_checked_at: datetime
    authorization_nonce_hash: str = Field(pattern=SHA256_REGEX)
    finality_evidence: dict[str, Any]
    proof_ref: str = Field(min_length=1)
    evidence_source: EvidenceSource
    submission_ref: str | None = None
    scenario: ScenarioRefModel | None = None

    def to_core(self) -> NoTransferProof:
        return NoTransferProof(
            reason_code=self.reason_code,
            checked_chain_id=self.checked_chain_id,
            attempt_count=self.attempt_count,
            first_checked_at=self.first_checked_at,
            last_checked_at=self.last_checked_at,
            authorization_nonce_hash=self.authorization_nonce_hash,
            finality_evidence=self.finality_evidence,
            proof_ref=self.proof_ref,
            evidence_source=self.evidence_source,
            submission_ref=self.submission_ref,
            scenario=self.scenario.to_core() if self.scenario is not None else None,
        )


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


class EvidenceHeadResponse(BaseModel):
    purchase_id: str
    event_count: int
    head_event_hash: str


class ExternalAnchorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchored_event_count: int = Field(ge=1)
    anchored_head_event_hash: str = Field(min_length=1)
    transaction_hash: str = Field(pattern=EVM_TRANSACTION_HASH_REGEX)
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


class SellerAgentSummaryResponse(BaseModel):
    seller_agent_id: str
    erc8004_agent_id: str | None
    signer_address: str | None
    identity_verified: bool
    quote_count: int
    reputation_status: str
    objective_feedback_value: int | None
    reputation_transaction_hash: str | None


class PublishIdentityResponse(BaseModel):
    chain_id: int
    registry_address: str
    purchase_id: str
    seller_agent_id: str
    tag1: str
    tag2: str


class ReputationDecisionResponse(BaseModel):
    decision: Literal["PUBLISH", "DEFER"]
    value: int | None
    reason_codes: list[str]
    audit_bundle_hash: str
    ruleset_version: str
    seller_agent_id: str
    erc8004_agent_id: str


class ReputationJobResponse(BaseModel):
    job_id: str
    status: Literal[
        "PENDING",
        "LEASED",
        "PREPARED",
        "SUBMITTED_UNKNOWN",
        "CONFIRMED",
        "DEFERRED",
        "CONFLICT",
    ]
    publish_identity: PublishIdentityResponse
    publish_identity_hash: str
    payload_fingerprint: str
    decision: ReputationDecisionResponse
    attempt_count: int
    worker_id: str | None
    lease_expires_at: datetime | None
    feedback_hash: str | None
    client_address: str | None
    feedback_uri: str | None
    transaction_ref: EvmTransactionRefModel | LocalTransactionRefModel | None
    receipt_proof_ref: str | None
    block_number: int | None
    log_index: int | None
    evidence_source: EvidenceSource | None
    confirmed_proof: JsonObject | None
    created_at: datetime
    updated_at: datetime


class InternalAuditFinalizeRequest(BaseModel):
    """Design 18.12.2: the finalize caller states the head it observed."""

    model_config = ConfigDict(extra="forbid")

    expected_event_count: int = Field(ge=1)
    expected_head_event_hash: str = Field(pattern=SHA256_REGEX)


class AuditFinalizeResponse(BaseModel):
    purchase_id: str
    finalized: bool
    reason: str
    audit: AuditReportResponse | None
    decision: ReputationDecisionResponse | None
    job: ReputationJobResponse | None


class InternalReputationClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1)
    lease_seconds: int = Field(ge=1, le=3_600)


class InternalReputationTransitionRequest(BaseModel):
    """Every outbox transition proves the lease owner and the immutable fingerprint."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1)
    payload_fingerprint: str = Field(pattern=SHA256_REGEX)


class InternalReputationPreparedRequest(InternalReputationTransitionRequest):
    """`PREPARED` is the whole pre-broadcast commitment, not just a hash."""

    transaction_ref: TransactionRefModel | None = None
    feedback_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    client_address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    feedback_uri: str = Field(min_length=1)


class InternalReputationSubmittedUnknownRequest(InternalReputationTransitionRequest):
    """A submission whose outcome is unknown may not even have a nameable reference."""

    transaction_ref: TransactionRefModel | None = None
    reason: str = Field(min_length=1)


class InternalReputationConflictRequest(BaseModel):
    """Design 18.6.1: a conflict is recorded, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    requested_fingerprint: str = Field(pattern=SHA256_REGEX)
    reason_code: str = Field(min_length=1, max_length=64)


class ConfirmedFeedbackProofModel(BaseModel):
    """`P6-AC-05.5`: a receipt reference plus the matching feedback event coordinates."""

    model_config = ConfigDict(extra="forbid")

    transaction_ref: TransactionRefModel
    receipt_proof_ref: str = Field(pattern=SHA256_REGEX)
    registry_address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    client_address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    erc8004_agent_id: str = Field(pattern=r"^[0-9]+$")
    value: Literal[0, 100]
    value_decimals: Literal[0]
    feedback_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    block_number: int = Field(ge=0)
    log_index: int = Field(ge=0)
    tag1: str = Field(min_length=1)
    tag2: str = Field(min_length=1)
    feedback_uri: str = Field(min_length=1)

    def to_core(self) -> ConfirmedFeedbackProof:
        return ConfirmedFeedbackProof(
            transaction_ref=self.transaction_ref.to_core(),
            receipt_proof_ref=self.receipt_proof_ref,
            registry_address=self.registry_address,
            client_address=self.client_address,
            erc8004_agent_id=self.erc8004_agent_id,
            value=self.value,
            value_decimals=self.value_decimals,
            feedback_hash=self.feedback_hash,
            block_number=self.block_number,
            log_index=self.log_index,
            tag1=self.tag1,
            tag2=self.tag2,
            feedback_uri=self.feedback_uri,
        )


class InternalReputationConfirmedRequest(InternalReputationTransitionRequest):
    proof: ConfirmedFeedbackProofModel


class ReputationOutboxEventResponse(BaseModel):
    """The atomic unit as one answer: the job moved *and* the evidence was appended."""

    job: ReputationJobResponse
    event: EventResponse


class ReputationSnapshotResponse(BaseModel):
    """`P6-AC-06.1`/`P6-AC-06.2`: the number never travels without its provenance."""

    snapshot_id: str
    seller_agent_id: str
    erc8004_agent_id: str
    chain_id: int
    registry_address: str
    trusted_clients: list[str]
    tag1: str
    tag2: str
    from_block: int
    to_block: int
    queried_at: datetime
    event_count: int
    raw_values: list[JsonObject]
    aggregation_method: str
    derived_score: float
    freshness_status: str
    freshness_age_seconds: int | None
    evidence_source: str
    snapshot_hash: str


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
