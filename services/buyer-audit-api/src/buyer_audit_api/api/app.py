from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, cast

import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from buyer_audit_api.api.schemas import (
    AegisDecisionEvidenceResponse,
    AegisPaymentReservationResponse,
    AegisPaymentTermsResponse,
    AuditAlertResponse,
    AuditFinalizeResponse,
    AuditFindingResponse,
    AuditReportResponse,
    BuyerWalletRequest,
    BuyerWalletResponse,
    ChallengeRequest,
    ChallengeResponse,
    DeliveryRecordRequest,
    DeliveryStageRequest,
    DeliveryStageResponse,
    EventResponse,
    EvidenceHeadResponse,
    ExternalAnchorRequest,
    InternalAegisAmbiguousSettlementRequest,
    InternalAegisDeliveryRequest,
    InternalAegisFailureRequest,
    InternalAegisSettlementRequest,
    InternalAuditFinalizeRequest,
    InternalConfirmMismatchRequest,
    InternalPaymentAuthorizeRequest,
    InternalPaymentClaimRequest,
    InternalPaymentFailureRequest,
    InternalPaymentReconciliationRequest,
    InternalPaymentSettlementRequest,
    InternalPaymentTransactionBindingRequest,
    InternalReconcileNoTransferRequest,
    InternalReconciliationCheckRequest,
    InternalReputationClaimRequest,
    InternalReputationConfirmedRequest,
    InternalReputationConflictRequest,
    InternalReputationPreparedRequest,
    InternalReputationSubmittedUnknownRequest,
    InternalTerminalProofRequest,
    MeResponse,
    PaymentIntentResponse,
    PaymentQuoteViewResponse,
    PaymentViewResponse,
    PublishIdentityResponse,
    PurchaseDetailResponse,
    PurchaseRequest,
    PurchaseResponse,
    PurchaseRunResponse,
    PurchaseSummaryResponse,
    ReputationDecisionResponse,
    ReputationJobResponse,
    ReputationOutboxEventResponse,
    ReputationSnapshotResponse,
    ScenarioResponse,
    SellerAgentSummaryResponse,
    SellerExecutionClaimRequest,
    SellerExecutionProviderSubmissionRequest,
    SellerExecutionResponse,
    SellerExecutionValueRequest,
    SellerQuoteTermsResponse,
    SensitivePayloadResponse,
    VerifyRequest,
    WalletDashboardResponse,
    WalletPolicyRequest,
    WalletPolicyResponse,
    transaction_ref_response,
)
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.aa_audit import is_aa_policy_request
from buyer_audit_api.core.aegis_payment import AegisPaymentService, AegisPaymentTerms
from buyer_audit_api.core.audit import (
    AuditReport,
    AuditReportReader,
    AuditRepository,
    AuditService,
)
from buyer_audit_api.core.errors import (
    AuthenticationError,
    DomainNotRegisteredError,
    EvidenceIntegrityError,
    EvidenceTransitionError,
    ExternalEvidenceError,
    PaymentConflictError,
    PaymentEvidenceError,
    PaymentPolicyError,
    SelectionAbortedError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    JsonObject,
    ScenarioMetadata,
    TransactionRef,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.payment import (
    PaymentIntent,
    PaymentIntentState,
    PaymentRepository,
    PaymentService,
    PaymentView,
    WalletPolicy,
)
from buyer_audit_api.core.ports import ReputationOutboxPort
from buyer_audit_api.core.projections import PurchaseProjection, PurchaseProjectionService
from buyer_audit_api.core.reputation import (
    ReputationDecision,
    ReputationOutboxConflict,
    ReputationPublishJob,
    ReputationSnapshot,
)
from buyer_audit_api.core.request_classification import StoredRequestClassifier
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState
from buyer_audit_api.domains.ai_inference.models import NormalizedAiRequest

SESSION_COOKIE = "pbl_session"

#: Endpoints of the superseded Phase 6 runtime: signed-quote seller execution, the
#: ERC-8004 reputation outbox, the Evidence Anchor and the independent receipt /
#: Transfer / AuthorizationUsed reconciliation surface. The `aa-three-factor-v1`
#: composition answers 404 for all of them, so nothing new can call them, while the
#: stored evidence they produced stays readable through the ordinary read paths.
_LEGACY_PAYMENT_INTENT_PATHS = frozenset(
    {
        "/internal/evidence/payment-intents/claim",
        "/internal/evidence/payment-intents/settle",
        "/internal/evidence/payment-intents/fail",
        "/internal/evidence/payment-intents/reconciliation-transaction",
        "/internal/evidence/payment-intents/reconciliation-checks",
        "/internal/evidence/payment-intents/confirm-mismatch",
        "/internal/evidence/payment-intents/reconcile-no-transfer",
    }
)
_LEGACY_PURCHASE_SUFFIXES = frozenset(
    {"payment-view", "external-anchors", "delivery", "delivery-stage", "finalize"}
)


def is_legacy_surface(path: str) -> bool:
    """True for a Phase 6 endpoint the new composition does not serve."""
    if path.startswith("/internal/evidence/seller-executions"):
        return True
    if path.startswith("/internal/evidence/reputation-outbox"):
        return True
    if path in _LEGACY_PAYMENT_INTENT_PATHS:
        return True
    if "/seller-quotes/" in path:
        return True
    return path.startswith("/internal/evidence/purchases/") and (
        path.rsplit("/", 1)[-1] in _LEGACY_PURCHASE_SUFFIXES
    )


_REDACTED_KEYS = {
    "apikey",
    "ciphertext",
    "privatekey",
    "prompt",
    "response",
    "signature",
}


def _redact(value: object) -> tuple[object, bool]:
    if isinstance(value, dict):
        output: dict[str, object] = {}
        changed = False
        for key, item in value.items():
            normalized = key.replace("_", "").lower()
            if normalized in _REDACTED_KEYS or normalized.endswith("signature"):
                output[key] = "<redacted>"
                changed = True
            else:
                redacted, nested_changed = _redact(item)
                output[key] = redacted
                changed = changed or nested_changed
        return output, changed
    if isinstance(value, list):
        output_list: list[object] = []
        changed = False
        for item in value:
            redacted, nested_changed = _redact(item)
            output_list.append(redacted)
            changed = changed or nested_changed
        return output_list, changed
    return value, False


_PROJECTIONS = PurchaseProjectionService()
_AUDIT_READER = AuditReportReader()
_TRANSACTION_PROOF_EVENT_TYPES = frozenset(
    {EventType.PAYMENT_SETTLED, EventType.PAYMENT_FAILED}
)


def _scenario_response(scenario: ScenarioMetadata | None) -> ScenarioResponse | None:
    if scenario is None:
        return None
    return ScenarioResponse(
        run_id=scenario.run_id,
        scenario_id=scenario.scenario_id,
        catalog_version=scenario.catalog_version,
    )


def _event_evidence_source(event: EvidenceEvent) -> EvidenceSource | None:
    raw = event.payload.get("evidenceSource")
    if not isinstance(raw, str):
        return None
    try:
        return EvidenceSource(raw)
    except ValueError:
        return None


def _event_transaction_ref(event: EvidenceEvent) -> TransactionRef | None:
    """Only validated unions reach the response; raw payload strings are never promoted."""
    raw = event.payload.get("transactionRef")
    if raw is not None:
        try:
            return transaction_ref_from_payload(raw)
        except ValueError:
            return None
    if event.type not in _TRANSACTION_PROOF_EVENT_TYPES:
        return None
    legacy_hash = event.payload.get("transactionHash")
    if not isinstance(legacy_hash, str):
        return None
    try:
        return transaction_ref_from_payload(
            {
                "kind": "EVM",
                "hash": legacy_hash.lower(),
                "chainId": 84532,
                "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
            }
        )
    except ValueError:
        return None


def _event_response(event: EvidenceEvent) -> EventResponse:
    payload, redacted = _redact(event.payload)
    assert isinstance(payload, dict)
    raw_scenario = event.payload.get("scenario")
    scenario: ScenarioMetadata | None = None
    if raw_scenario is not None:
        try:
            scenario = ScenarioMetadata.from_payload(raw_scenario)
        except ValueError:
            scenario = None
    return EventResponse(
        event_id=event.event_id,
        purchase_id=event.purchase_id,
        sequence=event.sequence,
        type=event.type.value,
        occurred_at=event.occurred_at,
        actor=event.actor,
        payload=payload,
        payload_hash=event.payload_hash,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
        evidence_refs=list(event.evidence_refs),
        redacted=redacted,
        evidence_source=_event_evidence_source(event),
        scenario=_scenario_response(scenario),
        transaction_ref=transaction_ref_response(_event_transaction_ref(event)),
    )


def _finding_responses(report: AuditReport) -> list[AuditFindingResponse]:
    return [
        AuditFindingResponse(
            code=item.code,
            severity=item.severity.value,
            title=item.title,
            detail=item.detail,
            evidence_refs=list(item.evidence_refs),
            authority=item.authority.value,
            rule_id=item.rule_id,
            ruleset_version=item.ruleset_version,
            expected=item.expected,
            observed=item.observed,
            mismatched_fields=list(item.mismatched_fields),
        )
        for item in report.findings
    ]


def _audit_response(report: AuditReport) -> AuditReportResponse:
    return AuditReportResponse(
        report_id=report.report_id,
        purchase_id=report.purchase_id,
        severity=report.severity.value,
        findings=_finding_responses(report),
        evidence_head_event_hash=report.evidence_head_event_hash,
        audit_bundle_hash=report.audit_bundle_hash,
        ruleset_version=report.ruleset_version,
    )


_OUTBOX_ERRORS = (
    PaymentEvidenceError,
    PaymentPolicyError,
    PaymentConflictError,
    EvidenceIntegrityError,
    EvidenceTransitionError,
    ValueError,
)


def _outbox_error(exc: Exception) -> HTTPException:
    """Design 18.12.2: stale lease/fingerprint 409, malformed proof 422, missing 404.

    A typed durable-state conflict answers with `{"reason", "message"}` so the Payment
    Executor can tell an ordinary lease/CAS race apart from a real immutable payload
    conflict. Only the latter may ever request conflict evidence.
    """
    if isinstance(exc, PaymentEvidenceError) and "not found" in str(exc):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReputationOutboxConflict):
        return HTTPException(
            status_code=409,
            detail={"reason": exc.reason.value, "message": str(exc)},
        )
    if isinstance(
        exc, (PaymentConflictError, EvidenceIntegrityError, EvidenceTransitionError)
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (PaymentEvidenceError, PaymentPolicyError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected reputation outbox error")


def _audit_report_response(report: AuditReport) -> AuditReportResponse:
    return _audit_response(report)


def _reputation_decision_response(
    decision: ReputationDecision,
) -> ReputationDecisionResponse:
    return ReputationDecisionResponse(
        decision=decision.kind.value,
        value=decision.value,
        reason_codes=[item.value for item in decision.reason_codes],
        audit_bundle_hash=decision.audit_bundle_hash,
        ruleset_version=decision.ruleset_version,
        seller_agent_id=decision.seller_agent_id,
        erc8004_agent_id=decision.erc8004_agent_id,
    )


def _reputation_job_response(job: ReputationPublishJob) -> ReputationJobResponse:
    return ReputationJobResponse(
        job_id=job.job_id,
        status=job.status.value,
        publish_identity=PublishIdentityResponse(
            chain_id=job.identity.chain_id,
            registry_address=job.identity.registry_address,
            purchase_id=job.identity.purchase_id,
            seller_agent_id=job.identity.seller_agent_id,
            tag1=job.identity.tag1,
            tag2=job.identity.tag2,
        ),
        publish_identity_hash=job.identity_hash,
        payload_fingerprint=job.payload_fingerprint,
        decision=_reputation_decision_response(job.decision),
        attempt_count=job.attempt_count,
        worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at,
        feedback_hash=job.feedback_hash,
        transaction_ref=transaction_ref_response(job.transaction_ref),
        receipt_proof_ref=job.receipt_proof_ref,
        client_address=job.client_address,
        feedback_uri=job.feedback_uri,
        block_number=job.block_number,
        log_index=job.log_index,
        evidence_source=job.evidence_source,
        confirmed_proof=(
            None if job.confirmed_proof is None else job.confirmed_proof.to_payload()
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _reputation_snapshot_response(
    snapshot: ReputationSnapshot,
) -> ReputationSnapshotResponse:
    return ReputationSnapshotResponse(
        snapshot_id=snapshot.snapshot_id,
        seller_agent_id=snapshot.seller_agent_id,
        erc8004_agent_id=snapshot.scope.erc8004_agent_id,
        chain_id=snapshot.scope.chain_id,
        registry_address=snapshot.scope.registry_address,
        trusted_clients=list(snapshot.scope.trusted_clients),
        tag1=snapshot.scope.tag1,
        tag2=snapshot.scope.tag2,
        from_block=snapshot.scope.from_block,
        to_block=snapshot.scope.to_block,
        queried_at=snapshot.queried_at,
        event_count=snapshot.event_count,
        raw_values=[event.to_payload() for event in snapshot.raw_values],
        aggregation_method=snapshot.aggregation_method,
        derived_score=snapshot.derived_score,
        freshness_status=snapshot.freshness_status.value,
        freshness_age_seconds=snapshot.freshness_age_seconds,
        evidence_source=snapshot.evidence_source.value,
        snapshot_hash=snapshot.snapshot_hash,
    )


def _summary_response(projection: PurchaseProjection) -> PurchaseSummaryResponse:
    return PurchaseSummaryResponse(
        purchase_id=projection.purchase_id,
        created_at=projection.created_at,
        domain=projection.domain,
        request_summary=projection.request_summary,
        status=projection.lifecycle_status,
        amount_units=projection.amount_units,
        token=projection.token,
        transaction_hash=projection.legacy_transaction_hash,
        audit_severity=projection.audit_severity,
        finding_count=projection.finding_count,
        audit_covers_head=projection.audit_covers_head,
        lifecycle_status=projection.lifecycle_status,
        payment_status=projection.payment_status.value,
        audit_status=projection.audit_status.value,
        evidence_source=projection.evidence_source,
        transaction_ref=transaction_ref_response(projection.transaction_ref),
        scenario=_scenario_response(projection.scenario),
        mismatched_fields=list(projection.mismatched_fields),
    )


def _summary(events: list[EvidenceEvent]) -> PurchaseSummaryResponse:
    return _summary_response(_PROJECTIONS.project(events))


def _payment_view_response(view: PaymentView) -> PaymentViewResponse:
    return PaymentViewResponse(
        purchase_id=view.purchase_id,
        owner_address=view.owner_address,
        buyer_wallet_address=view.buyer_wallet_address,
        budget_units=view.budget_units,
        request_policy=view.request_policy,
        decision_event_hash=view.decision_event_hash,
        quote=PaymentQuoteViewResponse(
            quote_id=view.quote.quote_id,
            seller_agent_id=view.quote.seller_agent_id,
            erc8004_agent_id=view.quote.erc8004_agent_id,
            provider_id=view.quote.provider_id,
            model_id=view.quote.model_id,
            model_version=view.quote.model_version,
            amount_units=view.quote.amount_units,
            token=view.quote.token,
            pay_to=view.quote.pay_to,
            expires_at=view.quote.expires_at,
            signer_address=view.quote.signer_address,
            chain_id=view.quote.chain_id,
            verifying_contract=view.quote.verifying_contract,
        ),
        event_count=view.event_count,
        head_event_hash=view.head_event_hash,
    )


def _payment_intent_response(intent: PaymentIntent) -> PaymentIntentResponse:
    return PaymentIntentResponse(
        purchase_id=intent.purchase_id,
        buyer_wallet_address=intent.buyer_wallet_address,
        policy_date=intent.policy_date,
        quote_id=intent.quote_id,
        decision_event_hash=intent.decision_event_hash,
        amount_units=intent.amount_units,
        token=intent.token,
        pay_to=intent.pay_to,
        permit2_nonce=intent.permit2_nonce,
        transfer_method=intent.transfer_method,
        authorization_nonce=intent.authorization_nonce,
        state=intent.state.value,
        claimed_at=intent.claimed_at,
        decision_authorization_hash=intent.decision_authorization_hash,
        decision_authorization_signature=intent.decision_authorization_signature,
        authorized_at=intent.authorized_at,
        reconciliation_reason=intent.reconciliation_reason,
        transaction_hash=intent.transaction_hash,
        block_number=intent.block_number,
        transfer_log_index=intent.transfer_log_index,
        settlement_verified_at=intent.settlement_verified_at,
        failure_reason=intent.failure_reason,
        failed_at=intent.failed_at,
        reconciliation_attempt_count=intent.reconciliation_attempt_count,
        reconciliation_first_checked_at=intent.reconciliation_first_checked_at,
        reconciliation_last_checked_at=intent.reconciliation_last_checked_at,
        reconciliation_last_outcome=(
            intent.reconciliation_last_outcome.value
            if intent.reconciliation_last_outcome is not None
            else None
        ),
        actual_transfer=(
            intent.actual_transfer.to_payload() if intent.actual_transfer is not None else None
        ),
        mismatched_fields=list(intent.mismatched_fields),
        terminal_outcome_key=intent.terminal_outcome_key,
        terminal_proof_ref=intent.terminal_proof_ref,
        terminal_evidence_source=intent.terminal_evidence_source,
        local_transaction_id=intent.local_transaction_id,
        no_transfer_reason_code=intent.no_transfer_reason_code,
    )


def create_app(container: AppContainer) -> FastAPI:
    payment_service = PaymentService(
        repository=cast(PaymentRepository, container.repository),
        clock=container.clock,
    )
    aegis_payments = AegisPaymentService(
        repository=cast(PaymentRepository, container.repository),
        clock=container.clock,
        execution_mode=container.aegis_execution_mode,
    )
    # aa-three-factor-v1 runs as a single-user local demo: the owner identity is server
    # configuration, never a browser-selected value, and SIWE is absent from this
    # composition rather than hidden behind the UI. An empty value keeps the historical
    # SIWE login surface for reading PBLC history.
    local_owner = (container.local_owner_address or "").strip().lower()
    allowed_origins = frozenset(
        origin.strip().lower().rstrip("/")
        for origin in container.local_allowed_origins
        if origin.strip()
    )
    allowed_hosts = frozenset(
        host.strip().lower() for host in container.local_allowed_hosts if host.strip()
    )
    audit_service = AuditService(
        repository=cast(AuditRepository, container.repository),
        clock=container.clock,
        # The audit decrypts the stored original request under its own authority to
        # re-derive the priority classification. Nothing but the classification result
        # leaves that boundary.
        original_requests=StoredRequestClassifier(
            store=container.repository,
            cipher=container.cipher,
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await container.repository.ensure_indexes()
        yield
        await container.repository.close()

    app = FastAPI(title="Buyer Agent & Audit Evidence API", version="0.1.0", lifespan=lifespan)

    if local_owner:
        # No CORS middleware is installed, so no wildcard and no reflected origin exists.
        # This guard additionally refuses a foreign Host (DNS rebinding) and any browser
        # request that declares a cross-site fetch, so a page on another origin cannot
        # drive the local owner's purchases even with credentials attached.
        @app.middleware("http")
        async def local_boundary(
            request: Request, call_next: Any
        ) -> Response:
            host = (request.headers.get("host") or "").split(":")[0].strip().lower()
            if host and host not in allowed_hosts:
                return JSONResponse(status_code=403, content={"detail": "host not allowed"})
            origin = request.headers.get("origin")
            if origin is not None and origin.strip().lower().rstrip("/") not in allowed_origins:
                return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
            fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return JSONResponse(
                    status_code=403, content={"detail": "cross-site request refused"}
                )
            if is_legacy_surface(request.url.path):
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": "endpoint is not part of the aa-three-factor-v1 runtime"
                    },
                )
            response: Response = await call_next(request)
            return response

    @app.exception_handler(EvidenceIntegrityError)
    async def evidence_integrity_handler(
        _request: Request, exc: EvidenceIntegrityError
    ) -> Response:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    async def verified_events(purchase_id: str) -> list[EvidenceEvent]:
        events = await container.repository.list_events(purchase_id)
        head = await container.repository.get_event_head(purchase_id)
        if not events and head is None:
            return []
        if head is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="evidence integrity check failed",
            )
        try:
            verify_event_chain(
                events,
                expected_event_count=head.event_count,
                expected_head_event_hash=head.head_event_hash,
            )
        except EvidenceIntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="evidence integrity check failed",
            ) from exc
        return events

    def require_owner(token: str | None) -> str:
        if local_owner:
            # The demo has exactly one owner. A cookie cannot select a different one.
            return local_owner
        if token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        try:
            return container.auth_service.read_session(token)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

    def require_internal(authorization: str | None) -> None:
        expected = f"Bearer {container.internal_service_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid internal service credential",
            )

    def require_admin(authorization: str | None) -> None:
        expected = f"Bearer {container.admin_service_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid admin service credential",
            )

    def payment_error(exc: Exception) -> HTTPException:
        if isinstance(exc, PaymentPolicyError):
            return HTTPException(status_code=422, detail=str(exc))
        if isinstance(
            exc,
            (
                PaymentEvidenceError,
                PaymentConflictError,
                EvidenceIntegrityError,
                EvidenceTransitionError,
            ),
        ):
            return HTTPException(status_code=409, detail=str(exc))
        return HTTPException(status_code=500, detail="unexpected payment error")

    def terminal_error(exc: Exception) -> HTTPException:
        """Design 18.12.2: stale state/head 409, malformed proof 422."""
        if isinstance(
            exc,
            (PaymentConflictError, EvidenceIntegrityError, EvidenceTransitionError),
        ):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, (PaymentEvidenceError, PaymentPolicyError, ValueError)):
            return HTTPException(status_code=422, detail=str(exc))
        return HTTPException(status_code=500, detail="unexpected payment error")

    async def terminal_context(
        body: InternalTerminalProofRequest,
        *,
        terminal_state: PaymentIntentState | None = None,
        attempt_number: int | None = None,
    ) -> tuple[PaymentIntent, list[EvidenceEvent]]:
        """M1: every terminal mutation proves the state and head it observed.

        A replay of evidence this purchase already holds skips the compare-and-set
        precondition, because the service then compares the complete immutable proof and
        answers with the committed result or a conflict.
        """
        intent = await payment_service.get_payment_intent(body.purchase_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="payment intent not found")
        events = await verified_events(body.purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        replayed = terminal_state is not None and intent.state is terminal_state
        if attempt_number is not None:
            replayed = replayed or any(
                event.type == EventType.PAYMENT_RECONCILIATION_CHECKED
                and event.payload.get("attemptNumber") == attempt_number
                for event in events
            )
        if replayed:
            return intent, events
        if intent.state != body.expected_state:
            raise HTTPException(status_code=409, detail="payment state changed before request")
        if body.expected_event_count != len(events):
            raise HTTPException(status_code=409, detail="evidence head changed before request")
        if body.expected_head_event_hash != events[-1].event_hash:
            raise HTTPException(status_code=409, detail="evidence head changed before request")
        return intent, events

    async def seller_execution_response(
        execution: SellerExecution,
    ) -> SellerExecutionResponse:
        async def read_value(payload_id: str | None) -> dict[str, object] | None:
            if payload_id is None:
                return None
            payload = await container.repository.get_sensitive_payload(payload_id)
            if payload is None or payload.purchase_id != execution.purchase_id:
                raise HTTPException(
                    status_code=409, detail="seller execution payload missing"
                )
            return container.cipher.decrypt_json(payload)

        prompt_value = await read_value(execution.prompt_payload_id)
        prompt = prompt_value.get("prompt") if prompt_value is not None else None
        if not isinstance(prompt, str):
            raise HTTPException(status_code=409, detail="seller execution prompt missing")
        return SellerExecutionResponse(
            purchase_id=execution.purchase_id,
            quote_id=execution.quote_id,
            seller_agent_id=execution.seller_agent_id,
            prompt=prompt,
            prompt_hash=execution.prompt_hash,
            payment_proof_hash=execution.payment_proof_hash,
            state=execution.state.value,
            authorization=await read_value(execution.authorization_payload_id),
            settlement=await read_value(execution.settlement_payload_id),
            provider_attempt_id=execution.provider_attempt_id,
            provider_attempt_token=execution.provider_attempt_token,
            result=await read_value(execution.result_payload_id),
        )

    async def transition_seller_execution_value(
        *,
        body: SellerExecutionValueRequest,
        expected_state: SellerExecutionState,
        next_state: SellerExecutionState,
        kind: str,
        field: str,
    ) -> SellerExecutionResponse:
        current = await container.repository.get_seller_execution(body.purchase_id)
        if current is None:
            raise HTTPException(status_code=404, detail="seller execution not found")
        if current.state != expected_state:
            return await seller_execution_response(current)
        sensitive = container.cipher.encrypt_json(
            purchase_id=body.purchase_id,
            kind=kind,
            payload=body.value,
            created_at=container.clock.now(),
        )
        await container.repository.store_sensitive_payload(sensitive)
        if field == "authorization_payload_id":
            updated = replace(
                current,
                state=next_state,
                authorization_payload_id=sensitive.payload_id,
            )
        elif field == "settlement_payload_id":
            updated = replace(
                current,
                state=next_state,
                settlement_payload_id=sensitive.payload_id,
            )
        elif field == "result_payload_id":
            updated = replace(
                current,
                state=next_state,
                result_payload_id=sensitive.payload_id,
            )
        else:
            raise ValueError("unsupported seller execution payload field")
        stored = await container.repository.transition_seller_execution(
            purchase_id=body.purchase_id,
            expected_state=expected_state,
            next_execution=updated,
        )
        return await seller_execution_response(stored)

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Server configuration only: the browser can disclose it but cannot select it.
        return {"status": "ok", "execution_mode": container.aegis_execution_mode}

    @app.post(
        "/internal/evidence/seller-executions/claim",
        response_model=SellerExecutionResponse,
    )
    async def claim_seller_execution(
        body: SellerExecutionClaimRequest,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        calculated = "sha256:" + hashlib.sha256(body.prompt.encode()).hexdigest()
        if not hmac.compare_digest(calculated, body.prompt_hash):
            raise HTTPException(status_code=409, detail="seller prompt hash mismatch")
        view = await payment_service.load_payment_view(body.purchase_id)
        if (
            view.quote.quote_id != body.quote_id
            or view.quote.seller_agent_id != body.seller_agent_id
        ):
            raise HTTPException(status_code=409, detail="seller execution quote mismatch")
        sensitive = container.cipher.encrypt_json(
            purchase_id=body.purchase_id,
            kind="seller-execution-prompt",
            payload={"prompt": body.prompt},
            created_at=container.clock.now(),
        )
        await container.repository.store_sensitive_payload(sensitive)
        requested = SellerExecution(
            purchase_id=body.purchase_id,
            quote_id=body.quote_id,
            seller_agent_id=body.seller_agent_id,
            prompt_hash=body.prompt_hash,
            payment_proof_hash=body.payment_proof_hash,
            prompt_payload_id=sensitive.payload_id,
            state=SellerExecutionState.CLAIMED,
            claimed_at=container.clock.now(),
        )
        stored = await container.repository.claim_seller_execution(requested)
        if (
            stored.quote_id != requested.quote_id
            or stored.seller_agent_id != requested.seller_agent_id
            or stored.prompt_hash != requested.prompt_hash
            or stored.payment_proof_hash != requested.payment_proof_hash
        ):
            raise HTTPException(status_code=409, detail="seller execution binding changed")
        return await seller_execution_response(stored)

    @app.get(
        "/internal/evidence/seller-executions/{purchase_id}",
        response_model=SellerExecutionResponse,
    )
    async def get_seller_execution(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        execution = await container.repository.get_seller_execution(purchase_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="seller execution not found")
        return await seller_execution_response(execution)

    @app.post(
        "/internal/evidence/seller-executions/authorization",
        response_model=SellerExecutionResponse,
    )
    async def record_seller_authorization(
        body: SellerExecutionValueRequest,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        return await transition_seller_execution_value(
            body=body,
            expected_state=SellerExecutionState.CLAIMED,
            next_state=SellerExecutionState.SUBMITTED,
            kind="seller-payment-authorization",
            field="authorization_payload_id",
        )

    @app.post(
        "/internal/evidence/seller-executions/settlement",
        response_model=SellerExecutionResponse,
    )
    async def record_seller_settlement(
        body: SellerExecutionValueRequest,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        return await transition_seller_execution_value(
            body=body,
            expected_state=SellerExecutionState.SUBMITTED,
            next_state=SellerExecutionState.SETTLED,
            kind="seller-payment-settlement",
            field="settlement_payload_id",
        )

    @app.post(
        "/internal/evidence/seller-executions/provider-submission",
        response_model=SellerExecutionResponse,
    )
    async def record_seller_provider_submission(
        body: SellerExecutionProviderSubmissionRequest,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        current = await container.repository.get_seller_execution(body.purchase_id)
        if current is None:
            raise HTTPException(status_code=404, detail="seller execution not found")
        if current.state == SellerExecutionState.PROVIDER_SUBMITTED:
            if current.provider_attempt_id != body.provider_attempt_id:
                raise HTTPException(
                    status_code=409, detail="seller provider attempt binding changed"
                )
            return await seller_execution_response(current)
        if current.state != SellerExecutionState.SETTLED:
            return await seller_execution_response(current)
        stored = await container.repository.transition_seller_execution(
            purchase_id=body.purchase_id,
            expected_state=SellerExecutionState.SETTLED,
            next_execution=replace(
                current,
                state=SellerExecutionState.PROVIDER_SUBMITTED,
                provider_attempt_id=body.provider_attempt_id,
                provider_attempt_token=body.provider_attempt_token,
            ),
        )
        if stored.provider_attempt_id != body.provider_attempt_id:
            raise HTTPException(
                status_code=409, detail="seller provider attempt binding changed"
            )
        return await seller_execution_response(stored)

    @app.post(
        "/internal/evidence/seller-executions/result",
        response_model=SellerExecutionResponse,
    )
    async def record_seller_result(
        body: SellerExecutionValueRequest,
        authorization: str | None = Header(default=None),
    ) -> SellerExecutionResponse:
        require_internal(authorization)
        return await transition_seller_execution_value(
            body=body,
            expected_state=SellerExecutionState.PROVIDER_SUBMITTED,
            next_state=SellerExecutionState.DELIVERED,
            kind="seller-provider-result",
            field="result_payload_id",
        )

    @app.put(
        "/internal/evidence/wallet-policies",
        response_model=WalletPolicyResponse,
    )
    async def put_wallet_policy(
        body: WalletPolicyRequest,
        authorization: str | None = Header(default=None),
    ) -> WalletPolicyResponse:
        require_internal(authorization)
        try:
            policy = WalletPolicy(
                buyer_wallet_address=body.buyer_wallet_address,
                policy_date=body.policy_date,
                token=body.token,
                per_transaction_limit_units=body.per_transaction_limit_units,
                daily_limit_units=body.daily_limit_units,
            )
            await payment_service.configure_wallet_policy(policy)
            stored = await payment_service.get_wallet_policy(
                buyer_wallet_address=policy.buyer_wallet_address,
                policy_date=policy.policy_date,
                token=policy.token,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PaymentConflictError as exc:
            raise payment_error(exc) from exc
        if stored is None:
            raise HTTPException(status_code=500, detail="wallet policy was not stored")
        return WalletPolicyResponse(
            buyer_wallet_address=stored.buyer_wallet_address,
            policy_date=stored.policy_date,
            token=stored.token,
            per_transaction_limit_units=stored.per_transaction_limit_units,
            daily_limit_units=stored.daily_limit_units,
            spent_units=stored.spent_units,
            reserved_units=stored.reserved_units,
        )

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/payment-view",
        response_model=PaymentViewResponse,
    )
    async def get_payment_view(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> PaymentViewResponse:
        require_internal(authorization)
        try:
            return _payment_view_response(await payment_service.load_payment_view(purchase_id))
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            EvidenceIntegrityError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/claim",
        response_model=PaymentIntentResponse,
    )
    async def claim_payment_intent(
        body: InternalPaymentClaimRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            return _payment_intent_response(await payment_service.claim(body.purchase_id))
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.get(
        "/internal/evidence/payment-intents/{purchase_id}",
        response_model=PaymentIntentResponse,
    )
    async def get_payment_intent(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        intent = await payment_service.get_payment_intent(purchase_id)
        if intent is None:
            raise HTTPException(status_code=404, detail="payment intent not found")
        return _payment_intent_response(intent)

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/head",
        response_model=EvidenceHeadResponse,
    )
    async def get_evidence_head(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> EvidenceHeadResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        return EvidenceHeadResponse(
            purchase_id=purchase_id,
            event_count=len(events),
            head_event_hash=events[-1].event_hash,
        )

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/external-anchors",
        response_model=EventResponse,
    )
    async def record_external_anchor(
        purchase_id: str,
        body: ExternalAnchorRequest,
        authorization: str | None = Header(default=None),
    ) -> EventResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        existing = [event for event in events if event.type == EventType.EVIDENCE_ANCHORED]
        if existing:
            event = existing[0]
            if (
                event.payload.get("transactionHash") == body.transaction_hash.lower()
                and event.payload.get("anchoredEventCount") == body.anchored_event_count
                and event.payload.get("anchoredHeadEventHash") == body.anchored_head_event_hash
            ):
                return _event_response(event)
            raise HTTPException(status_code=409, detail="different anchor already recorded")
        target_index = body.anchored_event_count - 1
        if (
            target_index >= len(events)
            or events[target_index].event_hash != body.anchored_head_event_hash
        ):
            raise HTTPException(
                status_code=409,
                detail="anchor does not match a verified evidence head",
            )
        try:
            event = await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.EVIDENCE_ANCHORED,
                occurred_at=container.clock.now(),
                actor={"id": "evidence-anchor-writer", "type": "service"},
                payload={
                    "anchoredEventCount": body.anchored_event_count,
                    "anchoredHeadEventHash": body.anchored_head_event_hash,
                    "chainId": body.chain_id,
                    "contractAddress": body.contract_address.lower(),
                    "transactionHash": body.transaction_hash.lower(),
                },
                evidence_refs=(
                    body.anchored_head_event_hash,
                    body.transaction_hash.lower(),
                ),
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
        except EvidenceTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _event_response(event)

    @app.post(
        "/internal/evidence/payment-intents/authorize",
        response_model=PaymentIntentResponse,
    )
    async def authorize_payment_intent(
        body: InternalPaymentAuthorizeRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            intent = await payment_service.authorize(
                purchase_id=body.purchase_id,
                authorization_hash=body.authorization_hash,
                signature=body.signature,
            )
            return _payment_intent_response(intent)
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/reconciliation",
        response_model=PaymentIntentResponse,
    )
    async def reconcile_payment_intent(
        body: InternalPaymentReconciliationRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            return _payment_intent_response(
                await payment_service.require_reconciliation(
                    purchase_id=body.purchase_id,
                    reason=body.reason,
                    transaction_hash=body.transaction_hash,
                    local_transaction_id=body.local_transaction_id,
                    scenario=(
                        body.scenario.to_core() if body.scenario is not None else None
                    ),
                )
            )
        except (
            PaymentEvidenceError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/reconciliation-transaction",
        response_model=PaymentIntentResponse,
    )
    async def bind_reconciliation_transaction(
        body: InternalPaymentTransactionBindingRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            return _payment_intent_response(
                await payment_service.bind_reconciliation_transaction(
                    purchase_id=body.purchase_id,
                    transaction_hash=body.transaction_hash,
                )
            )
        except (
            PaymentEvidenceError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/settle",
        response_model=PaymentIntentResponse,
    )
    async def settle_payment_intent(
        body: InternalPaymentSettlementRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            return _payment_intent_response(
                await payment_service.settle(
                    purchase_id=body.purchase_id,
                    transaction_hash=body.transaction_hash,
                    block_number=body.block_number,
                    transfer_log_index=body.transfer_log_index,
                    receipt_status=body.receipt_status,
                    token=body.token,
                    from_address=body.from_address,
                    to_address=body.to_address,
                    amount_units=body.amount_units,
                )
            )
        except (
            PaymentEvidenceError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/fail",
        response_model=PaymentIntentResponse,
    )
    async def fail_payment_intent(
        body: InternalPaymentFailureRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            return _payment_intent_response(
                await payment_service.fail_confirmed(
                    purchase_id=body.purchase_id,
                    reason=body.reason,
                    transaction_hash=body.transaction_hash,
                    block_number=body.block_number,
                    receipt_status=body.receipt_status,
                )
            )
        except (
            PaymentEvidenceError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/reconciliation-checks",
        response_model=PaymentIntentResponse,
    )
    async def record_reconciliation_check(
        body: InternalReconciliationCheckRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        await terminal_context(body, attempt_number=body.attempt_number)
        try:
            return _payment_intent_response(
                await payment_service.record_reconciliation_check(
                    purchase_id=body.purchase_id,
                    check=body.to_core(),
                )
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
            ValueError,
        ) as exc:
            raise terminal_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/confirm-mismatch",
        response_model=PaymentIntentResponse,
    )
    async def confirm_payment_mismatch(
        body: InternalConfirmMismatchRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        await terminal_context(
            body, terminal_state=PaymentIntentState.MISMATCH_CONFIRMED
        )
        try:
            return _payment_intent_response(
                await payment_service.confirm_mismatch(
                    purchase_id=body.purchase_id,
                    proof=body.to_core(),
                )
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
            ValueError,
        ) as exc:
            raise terminal_error(exc) from exc

    @app.post(
        "/internal/evidence/payment-intents/reconcile-no-transfer",
        response_model=PaymentIntentResponse,
    )
    async def reconcile_no_transfer(
        body: InternalReconcileNoTransferRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        await terminal_context(
            body, terminal_state=PaymentIntentState.RECONCILED_NO_TRANSFER
        )
        try:
            return _payment_intent_response(
                await payment_service.reconcile_no_transfer(
                    purchase_id=body.purchase_id,
                    proof=body.to_core(),
                )
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
            ValueError,
        ) as exc:
            raise terminal_error(exc) from exc

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/audit/finalize",
        response_model=AuditFinalizeResponse,
    )
    async def finalize_terminal_audit(
        purchase_id: str,
        body: InternalAuditFinalizeRequest,
        authorization: str | None = Header(default=None),
    ) -> AuditFinalizeResponse:
        """Design 18.7: the only writer of `AUDITED + REPUTATION_DECIDED` and the job."""
        require_internal(authorization)
        coordinator = container.terminal_coordinator
        if coordinator is None:
            raise HTTPException(
                status_code=503, detail="terminal audit coordinator is not configured"
            )
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        if body.expected_event_count != len(events):
            raise HTTPException(status_code=409, detail="evidence head changed before request")
        if body.expected_head_event_hash != events[-1].event_hash:
            raise HTTPException(status_code=409, detail="evidence head changed before request")
        try:
            result = await coordinator.finalize_if_eligible(purchase_id)
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
            ValueError,
        ) as exc:
            raise terminal_error(exc) from exc
        return AuditFinalizeResponse(
            purchase_id=result.purchase_id,
            finalized=result.finalized,
            reason=result.reason,
            audit=None if result.audit is None else _audit_report_response(result.audit),
            decision=(
                None
                if result.decision is None
                else _reputation_decision_response(result.decision)
            ),
            job=None if result.job is None else _reputation_job_response(result.job),
        )

    def require_outbox() -> ReputationOutboxPort:
        outbox = container.reputation_outbox
        if outbox is None:
            raise HTTPException(
                status_code=503, detail="reputation outbox is not configured"
            )
        return outbox

    @app.post(
        "/internal/evidence/reputation-outbox/claim",
        response_model=ReputationJobResponse,
    )
    async def claim_reputation_job(
        body: InternalReputationClaimRequest,
        authorization: str | None = Header(default=None),
    ) -> ReputationJobResponse:
        require_internal(authorization)
        outbox = require_outbox()
        now = container.clock.now()
        job = await outbox.claim_job(
            worker_id=body.worker_id,
            lease_until=now + timedelta(seconds=body.lease_seconds),
            now=now,
        )
        if job is None:
            raise HTTPException(status_code=404, detail="no claimable reputation job")
        return _reputation_job_response(job)

    @app.get(
        "/internal/evidence/reputation-outbox/{job_id}",
        response_model=ReputationJobResponse,
    )
    async def read_reputation_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> ReputationJobResponse:
        require_internal(authorization)
        job = await require_outbox().get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="reputation job not found")
        return _reputation_job_response(job)

    @app.post(
        "/internal/evidence/reputation-outbox/{job_id}/prepared",
        response_model=ReputationJobResponse,
    )
    async def mark_reputation_prepared(
        job_id: str,
        body: InternalReputationPreparedRequest,
        authorization: str | None = Header(default=None),
    ) -> ReputationJobResponse:
        require_internal(authorization)
        try:
            job = await require_outbox().mark_prepared(
                job_id=job_id,
                worker_id=body.worker_id,
                payload_fingerprint=body.payload_fingerprint,
                transaction_ref=(
                    None if body.transaction_ref is None else body.transaction_ref.to_core()
                ),
                feedback_hash=body.feedback_hash,
                client_address=body.client_address,
                feedback_uri=body.feedback_uri,
                now=container.clock.now(),
            )
        except _OUTBOX_ERRORS as exc:
            raise _outbox_error(exc) from exc
        return _reputation_job_response(job)

    @app.post(
        "/internal/evidence/reputation-outbox/{job_id}/submitted-unknown",
        response_model=ReputationJobResponse,
    )
    async def mark_reputation_submitted_unknown(
        job_id: str,
        body: InternalReputationSubmittedUnknownRequest,
        authorization: str | None = Header(default=None),
    ) -> ReputationJobResponse:
        require_internal(authorization)
        try:
            job = await require_outbox().mark_submitted_unknown(
                job_id=job_id,
                worker_id=body.worker_id,
                payload_fingerprint=body.payload_fingerprint,
                transaction_ref=(
                    None if body.transaction_ref is None else body.transaction_ref.to_core()
                ),
                reason=body.reason,
                now=container.clock.now(),
            )
        except _OUTBOX_ERRORS as exc:
            raise _outbox_error(exc) from exc
        return _reputation_job_response(job)

    @app.post(
        "/internal/evidence/reputation-outbox/{job_id}/confirmed",
        response_model=ReputationOutboxEventResponse,
    )
    async def mark_reputation_confirmed(
        job_id: str,
        body: InternalReputationConfirmedRequest,
        authorization: str | None = Header(default=None),
    ) -> ReputationOutboxEventResponse:
        """Design 18.8: the only path to `REPUTATION_RECORDED`, and it needs a proof."""
        require_internal(authorization)
        try:
            event, job = await require_outbox().mark_confirmed(
                job_id=job_id,
                worker_id=body.worker_id,
                payload_fingerprint=body.payload_fingerprint,
                proof=body.proof.to_core(),
                now=container.clock.now(),
            )
        except _OUTBOX_ERRORS as exc:
            raise _outbox_error(exc) from exc
        return ReputationOutboxEventResponse(
            job=_reputation_job_response(job), event=_event_response(event)
        )

    @app.post(
        "/internal/evidence/reputation-outbox/identity/{identity_hash}/conflict",
        response_model=ReputationOutboxEventResponse,
    )
    async def record_reputation_conflict(
        identity_hash: str,
        body: InternalReputationConflictRequest,
        authorization: str | None = Header(default=None),
    ) -> ReputationOutboxEventResponse:
        """A publication conflict always leaves append-only evidence behind."""
        require_internal(authorization)
        try:
            event, job = await require_outbox().record_conflict(
                identity_hash=identity_hash,
                requested_fingerprint=body.requested_fingerprint,
                reason_code=body.reason_code,
                now=container.clock.now(),
            )
        except _OUTBOX_ERRORS as exc:
            raise _outbox_error(exc) from exc
        return ReputationOutboxEventResponse(
            job=_reputation_job_response(job), event=_event_response(event)
        )

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/delivery",
        response_model=EventResponse,
    )
    async def record_delivery(
        purchase_id: str,
        body: DeliveryRecordRequest,
        authorization: str | None = Header(default=None),
    ) -> EventResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        existing = [event for event in events if event.type == EventType.DELIVERED]
        view = await payment_service.load_payment_view(purchase_id)
        if (
            body.seller_agent_id != view.quote.seller_agent_id
            or body.provider_id != view.quote.provider_id
            or body.model_id != view.quote.model_id
            or body.model_version != view.quote.model_version
        ):
            raise HTTPException(
                status_code=409, detail="delivery does not match selected signed quote"
            )
        payload = {
            "sellerAgentId": body.seller_agent_id,
            "providerId": body.provider_id,
            "modelId": body.model_id,
            "modelVersion": body.model_version,
            "responseHash": body.response_hash,
            "responseId": body.response_id,
        }
        if existing:
            if len(existing) == 1 and existing[0].payload == payload:
                return _event_response(existing[0])
            raise HTTPException(status_code=409, detail="different delivery already recorded")
        intent = await payment_service.get_payment_intent(purchase_id)
        if intent is None or intent.state.value != "SETTLED":
            raise HTTPException(status_code=409, detail="delivery requires settled payment")
        try:
            event = await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.DELIVERED,
                occurred_at=container.clock.now(),
                actor={"id": body.seller_agent_id, "type": "agent"},
                payload=payload,
                evidence_refs=(intent.transaction_hash or "", body.response_hash),
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
        except EvidenceTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _event_response(event)

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/delivery-stage",
        response_model=DeliveryStageResponse,
    )
    async def stage_delivery(
        purchase_id: str,
        body: DeliveryStageRequest,
        authorization: str | None = Header(default=None),
    ) -> DeliveryStageResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        existing = next(
            (event for event in events if event.type == EventType.DELIVERY_STAGED), None
        )
        if existing is not None:
            payload_id = str(existing.payload.get("sensitivePayloadId", ""))
            sensitive = await container.repository.get_sensitive_payload(payload_id)
            if sensitive is None or sensitive.purchase_id != purchase_id:
                raise HTTPException(status_code=409, detail="staged delivery payload missing")
            value = container.cipher.decrypt_json(sensitive)
            expected_hash = str(existing.payload.get("responseHash", ""))
            if sensitive.content_hash != expected_hash:
                raise HTTPException(status_code=409, detail="staged delivery hash mismatch")
            return DeliveryStageResponse(**value, response_hash=expected_hash)
        intent = await payment_service.get_payment_intent(purchase_id)
        if intent is None or intent.state.value not in {
            "AUTHORIZED",
            "RECONCILIATION_REQUIRED",
        }:
            raise HTTPException(status_code=409, detail="delivery staging requires authorization")
        view = await payment_service.load_payment_view(purchase_id)
        if (
            body.seller_agent_id != view.quote.seller_agent_id
            or body.provider_id != view.quote.provider_id
            or body.model_id != view.quote.model_id
            or body.model_version != view.quote.model_version
        ):
            raise HTTPException(
                status_code=409, detail="delivery does not match selected signed quote"
            )
        now = container.clock.now()
        value = body.model_dump(mode="json")
        sensitive = container.cipher.encrypt_json(
            purchase_id=purchase_id,
            kind="provider-result",
            payload=value,
            created_at=now,
        )
        await container.repository.store_sensitive_payload(sensitive)
        try:
            await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.DELIVERY_STAGED,
                occurred_at=now,
                actor={"id": "commerce-gateway", "type": "service"},
                payload={
                    "modelId": body.model_id,
                    "modelVersion": body.model_version,
                    "providerId": body.provider_id,
                    "responseHash": sensitive.content_hash,
                    "responseId": body.response_id,
                    "sellerAgentId": body.seller_agent_id,
                    "sensitivePayloadId": sensitive.payload_id,
                },
                evidence_refs=(sensitive.content_hash, intent.quote_id),
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
        except EvidenceTransitionError:
            raced = await verified_events(purchase_id)
            if any(event.type == EventType.DELIVERY_STAGED for event in raced):
                return await stage_delivery(purchase_id, body, authorization)
            raise
        return DeliveryStageResponse(**value, response_hash=sensitive.content_hash)

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/delivery-stage",
        response_model=DeliveryStageResponse,
    )
    async def get_staged_delivery(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> DeliveryStageResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        staged = next((event for event in events if event.type == EventType.DELIVERY_STAGED), None)
        if staged is None:
            raise HTTPException(status_code=404, detail="staged delivery not found")
        payload_id = str(staged.payload.get("sensitivePayloadId", ""))
        sensitive = await container.repository.get_sensitive_payload(payload_id)
        if sensitive is None or sensitive.purchase_id != purchase_id:
            raise HTTPException(status_code=409, detail="staged delivery payload missing")
        value = container.cipher.decrypt_json(sensitive)
        return DeliveryStageResponse(**value, response_hash=sensitive.content_hash)

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/audit",
        response_model=AuditReportResponse,
    )
    async def run_audit(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> AuditReportResponse:
        require_internal(authorization)
        try:
            return _audit_response(await audit_service.audit(purchase_id))
        except (EvidenceIntegrityError, EvidenceTransitionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The Phase 5 tx-only reputation surface is removed, not disabled. The writer
    # `POST .../purchases/{id}/reputation` appended `REPUTATION_RECORDED` from a
    # transaction-shaped hash with no receipt, no decoded `NewFeedback` event, no client,
    # no coordinates and no publish identity - exactly the proof-free success path this
    # packet must make unreachable. Its `GET .../reputation-intent` companion advertised
    # a feedback hash derived from the audit bundle, which is no longer the committed
    # payload: the outbox job carries the commitment and the audit bundle is the feedback
    # URI. Both are replaced by the durable outbox and its atomic confirmed transition.

    async def owner_purchase_events(owner: str) -> list[list[EvidenceEvent]]:
        purchase_ids = await container.repository.list_owner_purchase_ids(owner)
        result: list[list[EvidenceEvent]] = []
        for purchase_id in purchase_ids:
            events = await verified_events(purchase_id)
            if events and events[0].actor.get("id") == owner.lower():
                result.append(events)
        return result

    @app.get("/wallet", response_model=WalletDashboardResponse)
    async def wallet_dashboard(
        pbl_session: str | None = Cookie(default=None),
    ) -> WalletDashboardResponse:
        owner = require_owner(pbl_session)
        buyer = await container.auth_service.get_buyer_wallet(owner)
        policy = (
            await payment_service.get_latest_wallet_policy(buyer) if buyer is not None else None
        )
        token_balance_units: int | None = None
        balance_status = "rpc_not_configured"
        if buyer is not None and policy is not None and container.token_balance_reader is not None:
            try:
                token_balance_units = await container.token_balance_reader.balance_of(
                    token=policy.token,
                    wallet=buyer,
                )
                balance_status = "base_sepolia_verified"
            except (OSError, ValueError, httpx.HTTPError):
                balance_status = "rpc_unavailable"
        return WalletDashboardResponse(
            owner_address=owner,
            buyer_wallet_address=buyer,
            token=policy.token if policy is not None else None,
            token_balance_units=token_balance_units,
            balance_status=balance_status,
            policy_date=policy.policy_date if policy is not None else None,
            daily_limit_units=(policy.daily_limit_units if policy is not None else None),
            spent_units=policy.spent_units if policy is not None else 0,
            reserved_units=policy.reserved_units if policy is not None else 0,
        )

    @app.get("/purchases", response_model=list[PurchaseSummaryResponse])
    async def purchase_list(
        pbl_session: str | None = Cookie(default=None),
    ) -> list[PurchaseSummaryResponse]:
        owner = require_owner(pbl_session)
        return [_summary(events) for events in await owner_purchase_events(owner)]

    @app.get("/api/events")
    async def live_evidence_events(
        request: Request,
        pbl_session: str | None = Cookie(default=None),
    ) -> StreamingResponse:
        owner = require_owner(pbl_session)

        async def stream() -> AsyncIterator[str]:
            previous: dict[str, str] = {}
            yield "event: ready\ndata: {}\n\n"
            for tick in range(900):
                if await request.is_disconnected():
                    return
                current: dict[str, str] = {}
                for purchase_id in await container.repository.list_owner_purchase_ids(owner):
                    head = await container.repository.get_event_head(purchase_id)
                    if head is not None:
                        current[purchase_id] = head.head_event_hash
                if current != previous:
                    changed = sorted(
                        purchase_id
                        for purchase_id, head_hash in current.items()
                        if previous.get(purchase_id) != head_hash
                    )
                    payload = json.dumps({"purchaseIds": changed})
                    yield f"data: {payload}\n\n"
                    previous = current
                elif tick % 15 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/purchases/{purchase_id}", response_model=PurchaseDetailResponse)
    async def purchase_detail(
        purchase_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> PurchaseDetailResponse:
        owner = require_owner(pbl_session)
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        # Read-only: the persisted audit reader parses evidence and never appends.
        persisted = _AUDIT_READER.persisted(events)
        return PurchaseDetailResponse(
            summary=_summary(events),
            events=[_event_response(event) for event in events],
            audit=_audit_response(persisted) if persisted is not None else None,
        )

    @app.get("/audit-alerts", response_model=list[AuditAlertResponse])
    async def audit_alerts(
        pbl_session: str | None = Cookie(default=None),
    ) -> list[AuditAlertResponse]:
        owner = require_owner(pbl_session)
        alerts: list[AuditAlertResponse] = []
        for events in await owner_purchase_events(owner):
            report = _AUDIT_READER.persisted(events)
            if report is None:
                continue
            projection = _PROJECTIONS.project(events)
            alerts.extend(
                AuditAlertResponse(
                    purchase_id=report.purchase_id,
                    report_id=report.report_id,
                    code=finding.code,
                    severity=finding.severity.value,
                    title=finding.title,
                    detail=finding.detail,
                    evidence_refs=list(finding.evidence_refs),
                    authority=finding.authority.value,
                    rule_id=finding.rule_id,
                    ruleset_version=finding.ruleset_version,
                    expected=finding.expected,
                    observed=finding.observed,
                    mismatched_fields=list(finding.mismatched_fields),
                    evidence_source=projection.evidence_source,
                    scenario=_scenario_response(projection.scenario),
                )
                for finding in report.findings
            )
        return alerts

    @app.get("/agents", response_model=list[SellerAgentSummaryResponse])
    async def seller_agents(
        pbl_session: str | None = Cookie(default=None),
    ) -> list[SellerAgentSummaryResponse]:
        owner = require_owner(pbl_session)
        agents: dict[str, SellerAgentSummaryResponse] = {}
        for events in await owner_purchase_events(owner):
            quoted = next(
                (event for event in events if event.type == EventType.QUOTED),
                None,
            )
            if quoted is None:
                continue
            quotes = quoted.payload.get("signedQuotes", [])
            identities = quoted.payload.get("quoteIdentityEvidence", [])
            if not isinstance(quotes, list):
                continue
            for quote in quotes:
                if not isinstance(quote, dict):
                    continue
                agent_id = str(quote.get("seller_agent_id", "unknown"))
                identity = next(
                    (
                        item
                        for item in identities
                        if isinstance(item, dict) and item.get("quoteId") == quote.get("quote_id")
                    ),
                    {},
                )
                prior = agents.get(agent_id)
                reputation = next(
                    (
                        event
                        for event in events
                        if event.type == EventType.REPUTATION_RECORDED
                        and event.payload.get("erc8004AgentId") == identity.get("erc8004AgentId")
                    ),
                    None,
                )
                agents[agent_id] = SellerAgentSummaryResponse(
                    seller_agent_id=agent_id,
                    erc8004_agent_id=(
                        str(identity.get("erc8004AgentId"))
                        if identity.get("erc8004AgentId") is not None
                        else None
                    ),
                    signer_address=(
                        str(quote.get("signer_address"))
                        if quote.get("signer_address") is not None
                        else None
                    ),
                    identity_verified=identity.get("identityVerified") is True,
                    quote_count=(prior.quote_count + 1 if prior is not None else 1),
                    reputation_status=(
                        "on_chain_recorded" if reputation is not None else "chain_adapter_required"
                    ),
                    objective_feedback_value=(
                        int(reputation.payload["objectiveValue"])
                        if reputation is not None
                        else None
                    ),
                    reputation_transaction_hash=(
                        # A synthetic publication has no chain hash and must never be
                        # rendered as one; the typed reference lives in the event payload.
                        str(reputation.payload["transactionHash"])
                        if reputation is not None
                        and reputation.payload.get("transactionHash") is not None
                        else None
                    ),
                )
        return sorted(agents.values(), key=lambda item: item.seller_agent_id)

    if not local_owner:
        # Historical login surface. The `aa-three-factor-v1` composition never registers
        # it, so a new deployment has no SIWE endpoint to call rather than a hidden one.

        @app.post("/auth/siwe/challenge", response_model=ChallengeResponse)
        async def challenge(body: ChallengeRequest) -> ChallengeResponse:
            try:
                issued = await container.auth_service.create_challenge(body.owner_address)
            except AuthenticationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return ChallengeResponse(
                owner_address=issued.owner_address,
                nonce=issued.nonce,
                message=issued.message,
                expires_at=issued.expires_at,
            )

        @app.post("/auth/siwe/verify", status_code=status.HTTP_204_NO_CONTENT)
        async def verify(body: VerifyRequest, response: Response) -> None:
            try:
                token = await container.auth_service.verify_and_create_session(
                    message=body.message,
                    signature=body.signature,
                )
            except AuthenticationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(exc),
                ) from exc
            response.set_cookie(
                key=SESSION_COOKIE,
                value=token,
                httponly=True,
                secure=container.cookie_secure,
                samesite="lax",
                max_age=8 * 60 * 60,
                path="/",
            )

    @app.get("/auth/me", response_model=MeResponse)
    async def me(pbl_session: str | None = Cookie(default=None)) -> MeResponse:
        owner = require_owner(pbl_session)
        return MeResponse(
            owner_address=owner,
            buyer_wallet_address=await container.auth_service.get_buyer_wallet(owner),
        )

    @app.put("/internal/auth/buyer-wallet", response_model=BuyerWalletResponse)
    async def bind_buyer_wallet(
        body: BuyerWalletRequest,
        authorization: str | None = Header(default=None),
    ) -> BuyerWalletResponse:
        require_admin(authorization)
        try:
            buyer_wallet = await container.auth_service.bind_buyer_wallet(
                owner_address=body.owner_address,
                buyer_wallet_address=body.buyer_wallet_address,
            )
        except (AuthenticationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return BuyerWalletResponse(
            owner_address=body.owner_address.lower(),
            buyer_wallet_address=buyer_wallet,
        )

    @app.post("/purchases", response_model=PurchaseResponse, status_code=201)
    async def create_purchase(
        body: PurchaseRequest,
        pbl_session: str | None = Cookie(default=None),
    ) -> PurchaseResponse:
        owner = require_owner(pbl_session)
        try:
            budget_units = body.budget_units
            if budget_units is None:
                buyer_wallet = await container.auth_service.get_buyer_wallet(owner)
                if buyer_wallet is None:
                    raise PaymentPolicyError("buyer wallet is not bound")
                wallet_policy = await payment_service.get_latest_wallet_policy(buyer_wallet)
                if wallet_policy is None:
                    raise PaymentPolicyError("wallet policy is not configured")
                budget_units = wallet_policy.per_transaction_limit_units
            created = await container.purchase_service.create(
                owner_address=owner,
                domain_id=body.domain,
                raw_request=body.request,
                budget_units=budget_units,
                policy=body.policy,
            )
        except DomainNotRegisteredError as exc:
            raise HTTPException(status_code=422, detail=f"unknown domain: {exc}") from exc
        except PaymentPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            # A request the domain cannot normalize is a client error, and the message
            # names the accepted values instead of failing opaquely.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return PurchaseResponse(
            purchase_id=created.purchase_id,
            event_hash=created.event.event_hash,
            sensitive_payload_id=created.sensitive_payload_id,
        )

    @app.post("/purchases/{purchase_id}/decide", response_model=EventResponse)
    async def decide_purchase(
        purchase_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> EventResponse:
        owner = require_owner(pbl_session)
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        existing = next((event for event in events if event.type == EventType.DECIDED), None)
        if existing is not None:
            return _event_response(existing)
        requested_payload = events[0].payload
        if is_aa_policy_request(requested_payload):
            aegis = container.aegis_workflow
            if aegis is None:
                raise HTTPException(
                    status_code=503, detail="aa-three-factor-v1 workflow unavailable"
                )
            try:
                await aegis.decide(purchase_id=purchase_id)
            except (
                ExternalEvidenceError,
                SelectionAbortedError,
                ValueError,
                httpx.HTTPError,
                EvidenceTransitionError,
            ) as exc:
                raced = await verified_events(purchase_id)
                decided = next(
                    (event for event in raced if event.type == EventType.DECIDED), None
                )
                if decided is not None:
                    return _event_response(decided)
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            settled = await verified_events(purchase_id)
            return _event_response(
                next(event for event in settled if event.type == EventType.DECIDED)
            )
        workflow = container.ai_inference_workflow
        requested = events[0]
        if requested.payload.get("domain") != "ai_inference" or workflow is None:
            raise HTTPException(status_code=409, detail="domain decision workflow unavailable")
        try:
            normalized = NormalizedAiRequest.model_validate(
                requested.payload.get("normalizedRequest")
            )
            budget_units = requested.payload.get("budgetUnits")
            if not isinstance(budget_units, int) or isinstance(budget_units, bool):
                raise ValueError("persisted budget is malformed")
            await workflow.decide(
                purchase_id=purchase_id,
                normalized_request=normalized,
                budget_units=budget_units,
            )
        except (ValueError, httpx.HTTPError, EvidenceTransitionError) as exc:
            raced = await verified_events(purchase_id)
            decided = next((event for event in raced if event.type == EventType.DECIDED), None)
            if decided is not None:
                return _event_response(decided)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        completed = await verified_events(purchase_id)
        decided = next(event for event in completed if event.type == EventType.DECIDED)
        return _event_response(decided)

    @app.get(
        "/internal/purchases/{purchase_id}/aegis-decision",
        response_model=AegisDecisionEvidenceResponse,
    )
    async def aegis_decision_evidence(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> AegisDecisionEvidenceResponse:
        """Read-only source of truth for the gateway and the payment execution module."""
        require_internal(authorization)
        reader = container.aegis_evidence_reader
        if reader is None:
            raise HTTPException(
                status_code=503, detail="aa-three-factor-v1 evidence reader unavailable"
            )
        try:
            bundle = await reader.decision_evidence(purchase_id)
        except SelectionAbortedError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except EvidenceIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return AegisDecisionEvidenceResponse(
            purchase_id=str(bundle["purchaseId"]),
            decision=cast(JsonObject, bundle["decision"]),
            decision_event_hash=str(bundle["decisionEventHash"]),
            snapshot=cast(JsonObject, bundle["snapshot"]),
            snapshot_event_hash=str(bundle["snapshotEventHash"]),
            snapshot_hash=str(bundle["snapshotHash"]),
        )

    def aegis_terms_response(terms: AegisPaymentTerms) -> AegisPaymentTermsResponse:
        return AegisPaymentTermsResponse(
            purchase_id=terms.purchase_id,
            amount_units=terms.amount_units,
            budget_units=terms.budget_units,
            decision_event_hash=terms.decision_event_hash,
            snapshot_hash=terms.snapshot_hash,
            terms_binding_hash=terms.terms_binding_hash,
            provider_id=terms.provider_id,
            provider_model_id=terms.provider_model_id,
            model_version=terms.model_version,
            recipient=terms.recipient,
            token=terms.token,
        )

    @app.get(
        "/internal/evidence/aegis/purchases/{purchase_id}/payment-terms",
        response_model=AegisPaymentTermsResponse,
    )
    async def aegis_payment_terms(
        purchase_id: str,
        authorization: str | None = Header(default=None),
    ) -> AegisPaymentTermsResponse:
        """Server-derived terms. The Provider Gateway recomputes its 402 from these."""
        require_internal(authorization)
        try:
            return aegis_terms_response(await aegis_payments.terms(purchase_id))
        except PaymentEvidenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PaymentPolicyError, PaymentConflictError) as exc:
            raise payment_error(exc) from exc

    @app.post(
        "/internal/evidence/aegis/payments/reserve",
        response_model=AegisPaymentReservationResponse,
    )
    async def reserve_aegis_payment(
        body: InternalPaymentClaimRequest,
        authorization: str | None = Header(default=None),
    ) -> AegisPaymentReservationResponse:
        """One durable attempt per purchaseId, amount and recipient recomputed here."""
        require_internal(authorization)
        try:
            terms, intent = await aegis_payments.reserve(body.purchase_id)
        except PaymentEvidenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PaymentPolicyError, PaymentConflictError, EvidenceTransitionError) as exc:
            raise payment_error(exc) from exc
        return AegisPaymentReservationResponse(
            terms=aegis_terms_response(terms),
            intent=_payment_intent_response(intent),
        )

    @app.post(
        "/internal/evidence/aegis/payments/settle",
        response_model=PaymentIntentResponse,
    )
    async def settle_aegis_payment(
        body: InternalAegisSettlementRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        """The Facilitator answer is the payment status basis, not a chain verification."""
        require_internal(authorization)
        try:
            intent = await aegis_payments.settle(
                purchase_id=body.purchase_id,
                facilitator_transaction=body.facilitator_transaction,
                facilitator_network=body.facilitator_network,
                facilitator_payer=body.facilitator_payer,
                facilitator_amount=body.facilitator_amount,
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc
        return _payment_intent_response(intent)

    @app.post(
        "/internal/evidence/aegis/payments/fail",
        response_model=PaymentIntentResponse,
    )
    async def fail_aegis_payment(
        body: InternalAegisFailureRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        require_internal(authorization)
        try:
            intent = await aegis_payments.fail(
                purchase_id=body.purchase_id,
                reason=body.reason,
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc
        return _payment_intent_response(intent)

    @app.post(
        "/internal/evidence/aegis/payments/ambiguous",
        response_model=PaymentIntentResponse,
    )
    async def record_ambiguous_aegis_settlement(
        body: InternalAegisAmbiguousSettlementRequest,
        authorization: str | None = Header(default=None),
    ) -> PaymentIntentResponse:
        """A contradictory success is parked with its original answer, never failed.

        The reservation is deliberately kept: the attempt may or may not have moved money,
        and releasing the budget would let a second payment be made for the same purchase.
        """
        require_internal(authorization)
        try:
            intent = await aegis_payments.record_ambiguous_settlement(
                purchase_id=body.purchase_id,
                mismatch_reason=body.mismatch_reason,
                success=body.success,
                network=body.network,
                transaction=body.transaction,
                payer=body.payer,
                amount=body.amount,
            )
        except (
            PaymentEvidenceError,
            PaymentPolicyError,
            PaymentConflictError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc
        return _payment_intent_response(intent)

    @app.post(
        "/internal/evidence/aegis/purchases/{purchase_id}/delivery",
        response_model=EventResponse,
    )
    async def record_aegis_delivery(
        purchase_id: str,
        body: InternalAegisDeliveryRequest,
        authorization: str | None = Header(default=None),
    ) -> EventResponse:
        """Delivery is only recordable against the settled, decided model identity."""
        require_internal(authorization)
        try:
            terms, _, events = await aegis_payments.delivery_context(purchase_id)
        except PaymentEvidenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PaymentPolicyError, PaymentConflictError) as exc:
            raise payment_error(exc) from exc
        if (
            body.provider_id != terms.provider_id
            or body.provider_model_id != terms.provider_model_id
            or body.model_version != terms.model_version
        ):
            raise HTTPException(
                status_code=409, detail="delivery does not match the selected model"
            )
        payload: JsonObject = {
            "executionMode": aegis_payments.execution_mode,
            "modelId": body.provider_model_id,
            "modelVersion": body.model_version,
            "observedExecutionMs": body.observed_execution_ms,
            "providerId": body.provider_id,
            "responseHash": body.response_hash,
            "responseId": body.response_id,
            "termsBindingHash": terms.terms_binding_hash,
        }
        existing = [event for event in events if event.type == EventType.DELIVERED]
        if existing:
            # Identity, not the whole payload: `observedExecutionMs` is how long this
            # mock execution happened to take, so a repeated delivery of the same result
            # would otherwise conflict with itself on a one millisecond difference.
            identity = (
                "executionMode",
                "modelId",
                "modelVersion",
                "providerId",
                "responseHash",
                "responseId",
                "termsBindingHash",
            )
            if len(existing) == 1 and all(
                existing[0].payload.get(key) == payload[key] for key in identity
            ):
                return _event_response(existing[0])
            raise HTTPException(status_code=409, detail="different delivery already recorded")
        try:
            event = await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.DELIVERED,
                occurred_at=container.clock.now(),
                actor={"id": f"{body.provider_id}-provider-gateway", "type": "service"},
                payload=payload,
                evidence_refs=(terms.terms_binding_hash, body.response_hash),
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
        except EvidenceTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _event_response(event)

    @app.post("/purchases/{purchase_id}/run", response_model=PurchaseRunResponse)
    async def run_purchase(
        purchase_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> PurchaseRunResponse:
        owner = require_owner(pbl_session)
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        if container.commerce_gateway is None:
            raise HTTPException(status_code=503, detail="payment executor unavailable")
        await decide_purchase(purchase_id, pbl_session)
        events = await verified_events(purchase_id)
        requested = events[0]
        payload_id = requested.payload.get("sensitivePayloadId")
        if not isinstance(payload_id, str):
            raise HTTPException(status_code=409, detail="request payload reference missing")
        sensitive = await container.repository.get_sensitive_payload(payload_id)
        if sensitive is None or sensitive.purchase_id != purchase_id:
            raise HTTPException(status_code=409, detail="request payload missing")
        raw_request = container.cipher.decrypt_json(sensitive)
        prompt = raw_request.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise HTTPException(status_code=409, detail="request prompt missing")
        try:
            payment = await container.commerce_gateway.execute(
                purchase_id=purchase_id,
                resource_body={"prompt": prompt},
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        report = await audit_service.audit(purchase_id)
        return PurchaseRunResponse(
            purchase_id=purchase_id,
            payment=payment,
            audit=_audit_response(report),
        )

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/seller-quotes/{quote_id}",
        response_model=SellerQuoteTermsResponse,
    )
    async def seller_quote_terms(
        purchase_id: str,
        quote_id: str,
        authorization: str | None = Header(default=None),
    ) -> SellerQuoteTermsResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        quoted = next((event for event in events if event.type == EventType.QUOTED), None)
        quotes = quoted.payload.get("signedQuotes", []) if quoted else []
        match = next(
            (
                item
                for item in quotes
                if isinstance(item, dict) and item.get("quote_id") == quote_id
            ),
            None,
        )
        if match is None:
            raise HTTPException(status_code=404, detail="seller quote not found")
        return SellerQuoteTermsResponse(
            purchase_id=purchase_id,
            quote_id=quote_id,
            model_id=str(match["model_id"]),
            amount_units=int(match["amount_units"]),
            token=str(match["token"]),
            pay_to=str(match["pay_to"]),
            expires_at=datetime.fromisoformat(str(match["expires_at"])),
        )

    @app.get("/purchases/{purchase_id}/events", response_model=list[EventResponse])
    async def list_events(
        purchase_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> list[EventResponse]:
        owner = require_owner(pbl_session)
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        return [_event_response(event) for event in events]

    @app.post(
        "/purchases/{purchase_id}/sensitive/{payload_id}/access",
        response_model=SensitivePayloadResponse,
        status_code=201,
    )
    async def access_sensitive(
        purchase_id: str,
        payload_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> SensitivePayloadResponse:
        """H4: reading sensitive material is an explicit mutation, never a GET.

        Every read of an encrypted payload must leave an append-only access record, so
        this operation is a POST and the GET/read contract stays strictly zero-write.
        """
        owner = require_owner(pbl_session)
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        payload = await container.repository.get_sensitive_payload(payload_id)
        if payload is None or payload.purchase_id != purchase_id:
            raise HTTPException(status_code=404)
        value = container.cipher.decrypt_json(payload)
        await container.repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.SENSITIVE_PAYLOAD_ACCESSED,
            occurred_at=container.clock.now(),
            actor={"id": owner.lower(), "type": "user"},
            payload={"kind": payload.kind, "payloadId": payload.payload_id},
        )
        return SensitivePayloadResponse(
            purchase_id=purchase_id,
            payload_id=payload.payload_id,
            kind=payload.kind,
            content_hash=payload.content_hash,
            value=value,
        )

    return app
