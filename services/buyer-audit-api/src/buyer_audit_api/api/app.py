from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, cast

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from buyer_audit_api.api.schemas import (
    AegisDecisionEvidenceResponse,
    AegisPaymentReservationResponse,
    AegisPaymentTermsResponse,
    AuditAlertResponse,
    AuditFindingResponse,
    AuditReportResponse,
    BuyerWalletRequest,
    BuyerWalletResponse,
    CheckpointRecordResponse,
    EventResponse,
    EvidenceCheckpointRecordRequest,
    EvidenceCheckpointResponse,
    InternalAegisAmbiguousSettlementRequest,
    InternalAegisDeliveryRequest,
    InternalAegisFailureRequest,
    InternalAegisSettlementRequest,
    InternalPaymentAuthorizeRequest,
    InternalPaymentClaimRequest,
    InternalPaymentReconciliationRequest,
    PaymentIntentResponse,
    PurchaseDetailResponse,
    PurchaseRequest,
    PurchaseResponse,
    PurchaseRunResponse,
    PurchaseSummaryResponse,
    ScenarioResponse,
    SensitivePayloadResponse,
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
    ConfigurationError,
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
from buyer_audit_api.core.observer import (
    ObserverFailureCode,
    ObserverMode,
    ObserverPhase,
    ObserverUnavailable,
)
from buyer_audit_api.core.payment import (
    PaymentIntent,
    PaymentRepository,
    PaymentService,
    WalletPolicy,
)
from buyer_audit_api.core.projections import PurchaseProjection, PurchaseProjectionService
from buyer_audit_api.core.request_classification import StoredRequestClassifier

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
_TRANSACTION_PROOF_EVENT_TYPES = frozenset({EventType.PAYMENT_SETTLED, EventType.PAYMENT_FAILED})


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
    # configuration, never a browser-selected value. There is no login surface.
    local_owner = container.local_owner_address.strip().lower()
    if not local_owner:
        raise ConfigurationError("local owner address is required")
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

    # No CORS middleware is installed, so no wildcard and no reflected origin exists.
    # This guard additionally refuses a foreign Host (DNS rebinding) and any browser
    # request that declares a cross-site fetch, so a page on another origin cannot
    # drive the local owner's purchases even with credentials attached.
    @app.middleware("http")
    async def local_boundary(request: Request, call_next: Any) -> Response:
        host = (request.headers.get("host") or "").split(":")[0].strip().lower()
        if host and host not in allowed_hosts:
            return JSONResponse(status_code=403, content={"detail": "host not allowed"})
        origin = request.headers.get("origin")
        if origin is not None and origin.strip().lower().rstrip("/") not in allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return JSONResponse(status_code=403, content={"detail": "cross-site request refused"})
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

    def require_owner() -> str:
        # The demo has exactly one owner. No request header or cookie can select another.
        return local_owner

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

    async def observe_phase(purchase_id: str, phase: ObserverPhase) -> None:
        """Append one advisory outcome, then freeze the prefix that may be anchored.

        This is deliberately best-effort: a local model timeout creates visible evidence
        rather than being rewritten as a clean observation.  It never changes payment
        eligibility; the caller decides whether an enabled checkpoint is a hard gate.
        """
        if container.observer_mode == ObserverMode.OFF.value:
            return
        events = await verified_events(purchase_id)
        selected = [
            event
            for event in events
            if event.type == EventType.CHECKPOINT_TARGET_SELECTED
            and event.payload.get("phase") == phase.value
        ]
        if selected:
            return
        needed = EventType.DECIDED if phase is ObserverPhase.DECISION else EventType.AUDITED
        if not any(event.type == needed for event in events):
            raise HTTPException(
                status_code=409, detail=f"{phase.value} checkpoint prerequisite missing"
            )
        prior = [
            event
            for event in events
            if event.type in {EventType.OBSERVER_COMPLETED, EventType.OBSERVER_UNAVAILABLE}
            and event.payload.get("phase") == phase.value
        ]
        if prior:
            # An interrupted coordinator may have written the observation but not the
            # target marker. Reuse that exact prefix; never run the observer twice.
            observed = prior[-1]
            try:
                await container.repository.append_event(
                    purchase_id=purchase_id,
                    event_type=EventType.CHECKPOINT_TARGET_SELECTED,
                    occurred_at=container.clock.now(),
                    actor={"id": "checkpoint-coordinator", "type": "service"},
                    payload={
                        "phase": phase.value,
                        "eventCount": observed.sequence,
                        "headEventHash": observed.event_hash,
                    },
                    evidence_refs=(observed.event_hash,),
                    expected_event_count=len(events),
                    expected_head_event_hash=events[-1].event_hash,
                )
            except EvidenceTransitionError:
                raced = await verified_events(purchase_id)
                if not any(
                    event.type == EventType.CHECKPOINT_TARGET_SELECTED
                    and event.payload.get("phase") == phase.value
                    for event in raced
                ):
                    raise
            return
        prompt: str | None = None
        if container.observer_mode == ObserverMode.LOCAL_QWEN.value:
            requested = events[0]
            payload_id = requested.payload.get("sensitivePayloadId")
            if isinstance(payload_id, str):
                sensitive_payload = await container.repository.get_sensitive_payload(payload_id)
                if sensitive_payload is not None and sensitive_payload.purchase_id == purchase_id:
                    raw = container.cipher.decrypt_json(sensitive_payload)
                    candidate = raw.get("prompt")
                    prompt = candidate if isinstance(candidate, str) else None
        current_count, current_hash = len(events), events[-1].event_hash
        observer_payload: JsonObject
        if container.observer_mode == ObserverMode.MOCK.value:
            kind, observer_payload = (
                EventType.OBSERVER_COMPLETED,
                {"phase": phase.value, "mode": "mock", "model": "mock-observer-v1", "findings": []},
            )
        else:
            try:
                if container.evidence_observer is None:
                    raise ObserverUnavailable(ObserverFailureCode.NOT_CONFIGURED)
                findings = await container.evidence_observer.observe(
                    phase=phase, prompt=prompt, events=tuple(events)
                )
                kind, observer_payload = (
                    EventType.OBSERVER_COMPLETED,
                    {
                        "phase": phase.value,
                        "mode": "local-qwen",
                        "model": container.evidence_observer.model,
                        "findings": [item.payload() for item in findings],
                    },
                )
            except ObserverUnavailable as exc:
                kind, observer_payload = (
                    EventType.OBSERVER_UNAVAILABLE,
                    {
                        "phase": phase.value,
                        "mode": "local-qwen",
                        "status": "unavailable",
                        "diagnosticCode": exc.code.value,
                        "reason": "local observer unavailable",
                    },
                )
        try:
            observed = await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=kind,
                occurred_at=container.clock.now(),
                actor={"id": "local-evidence-observer", "type": "service"},
                payload=observer_payload,
                evidence_refs=tuple(event.event_id for event in events),
                expected_event_count=current_count,
                expected_head_event_hash=current_hash,
            )
            await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.CHECKPOINT_TARGET_SELECTED,
                occurred_at=container.clock.now(),
                actor={"id": "checkpoint-coordinator", "type": "service"},
                payload={
                    "phase": phase.value,
                    "eventCount": observed.sequence,
                    "headEventHash": observed.event_hash,
                },
                evidence_refs=(observed.event_hash,),
                expected_event_count=observed.sequence,
                expected_head_event_hash=observed.event_hash,
            )
        except EvidenceTransitionError:
            # A racing request can only have won by selecting this same immutable phase.
            raced = await verified_events(purchase_id)
            if any(
                event.type == EventType.CHECKPOINT_TARGET_SELECTED
                and event.payload.get("phase") == phase.value
                for event in raced
            ):
                return
            raise

    def checkpoint_response(
        events: list[EvidenceEvent], phase: ObserverPhase
    ) -> EvidenceCheckpointResponse:
        targets = [
            event
            for event in events
            if event.type == EventType.CHECKPOINT_TARGET_SELECTED
            and event.payload.get("phase") == phase.value
        ]
        if len(targets) != 1:
            raise HTTPException(status_code=409, detail=f"{phase.value} checkpoint target missing")
        target = targets[0].payload
        count, head_hash = target.get("eventCount"), target.get("headEventHash")
        if not isinstance(count, int) or count < 1 or not isinstance(head_hash, str):
            raise HTTPException(status_code=409, detail="checkpoint target is malformed")
        if count > len(events) or events[count - 1].event_hash != head_hash:
            raise HTTPException(
                status_code=409, detail="checkpoint target no longer matches evidence"
            )
        records = [
            event
            for event in events
            if event.type == EventType.CHECKPOINT_RECORDED
            and event.payload.get("phase") == phase.value
        ]
        if len(records) > 1:
            raise HTTPException(status_code=409, detail="multiple checkpoint records")
        record: CheckpointRecordResponse | None = None
        if records:
            value = records[0].payload
            record = CheckpointRecordResponse(
                mode=cast(Literal["mock", "live"], value["mode"]),
                eventCount=int(value["eventCount"]),
                headEventHash=str(value["headEventHash"]),
                transactionHash=(
                    str(value["transactionHash"])
                    if value.get("transactionHash") is not None
                    else None
                ),
                chainId=int(value["chainId"]) if value.get("chainId") is not None else None,
                contractAddress=(
                    str(value["contractAddress"])
                    if value.get("contractAddress") is not None
                    else None
                ),
            )
        return EvidenceCheckpointResponse(
            purchaseId=events[0].purchase_id,
            phase=phase.value,
            eventCount=count,
            headEventHash=head_hash,
            record=record,
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

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Server configuration only: the browser can disclose it but cannot select it.
        return {
            "status": "ok",
            "execution_mode": container.aegis_execution_mode,
            "token_symbol": container.payment_token_symbol,
        }

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
                    scenario=(body.scenario.to_core() if body.scenario is not None else None),
                )
            )
        except (
            PaymentEvidenceError,
            PaymentConflictError,
            EvidenceIntegrityError,
            EvidenceTransitionError,
        ) as exc:
            raise payment_error(exc) from exc

    async def owner_purchase_events(owner: str) -> list[list[EvidenceEvent]]:
        purchase_ids = await container.repository.list_owner_purchase_ids(owner)
        result: list[list[EvidenceEvent]] = []
        for purchase_id in purchase_ids:
            events = await verified_events(purchase_id)
            if events and events[0].actor.get("id") == owner.lower():
                result.append(events)
        return result

    @app.get("/wallet", response_model=WalletDashboardResponse)
    async def wallet_dashboard() -> WalletDashboardResponse:
        owner = require_owner()
        buyer = await container.auth_service.get_buyer_wallet(owner)
        policy = (
            await payment_service.get_latest_wallet_policy(buyer) if buyer is not None else None
        )
        # This composition holds no chain reader, so no balance is ever queried and the
        # dashboard is told so instead of being shown an unqueried number.
        return WalletDashboardResponse(
            owner_address=owner,
            buyer_wallet_address=buyer,
            token=policy.token if policy is not None else None,
            token_balance_units=None,
            balance_status="rpc_not_configured",
            policy_date=policy.policy_date if policy is not None else None,
            daily_limit_units=(policy.daily_limit_units if policy is not None else None),
            spent_units=policy.spent_units if policy is not None else 0,
            reserved_units=policy.reserved_units if policy is not None else 0,
        )

    @app.get("/purchases", response_model=list[PurchaseSummaryResponse])
    async def purchase_list() -> list[PurchaseSummaryResponse]:
        owner = require_owner()
        return [_summary(events) for events in await owner_purchase_events(owner)]

    @app.get("/api/events")
    async def live_evidence_events(request: Request) -> StreamingResponse:
        owner = require_owner()

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
    async def purchase_detail(purchase_id: str) -> PurchaseDetailResponse:
        owner = require_owner()
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
    async def audit_alerts() -> list[AuditAlertResponse]:
        owner = require_owner()
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
    async def create_purchase(body: PurchaseRequest) -> PurchaseResponse:
        owner = require_owner()
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
    async def decide_purchase(purchase_id: str) -> EventResponse:
        owner = require_owner()
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        existing = next((event for event in events if event.type == EventType.DECIDED), None)
        if existing is not None:
            return _event_response(existing)
        if not is_aa_policy_request(events[0].payload):
            # A purchase requested under a superseded policy stays readable but is never
            # decided by this runtime: there is no other decision workflow to fall back to.
            raise HTTPException(
                status_code=409, detail="purchase was not requested under aa-three-factor-v1"
            )
        aegis = container.aegis_workflow
        if aegis is None:
            raise HTTPException(status_code=503, detail="aa-three-factor-v1 workflow unavailable")
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
            decided = next((event for event in raced if event.type == EventType.DECIDED), None)
            if decided is not None:
                return _event_response(decided)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        settled = await verified_events(purchase_id)
        return _event_response(next(event for event in settled if event.type == EventType.DECIDED))

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
    async def run_purchase(purchase_id: str) -> PurchaseRunResponse:
        owner = require_owner()
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        if container.commerce_gateway is None:
            raise HTTPException(status_code=503, detail="payment executor unavailable")
        await decide_purchase(purchase_id)
        await observe_phase(purchase_id, ObserverPhase.DECISION)
        # An enabled decision checkpoint is a payment gate.  The gateway independently
        # fetches this frozen prefix and refuses to execute unless its own record agrees.
        if container.checkpoint_mode != "off":
            try:
                await container.commerce_gateway.checkpoint(
                    purchase_id=purchase_id, phase=ObserverPhase.DECISION.value
                )
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(
                    status_code=502, detail="decision evidence checkpoint failed"
                ) from exc
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
        await observe_phase(purchase_id, ObserverPhase.AUDIT)
        # Payment already completed. A failed audit anchor is visible/recoverable and
        # must never trigger a second execution of the payment endpoint.
        if container.checkpoint_mode != "off":
            try:
                await container.commerce_gateway.checkpoint(
                    purchase_id=purchase_id, phase=ObserverPhase.AUDIT.value
                )
            except (httpx.HTTPError, ValueError):
                pass
        return PurchaseRunResponse(
            purchase_id=purchase_id,
            payment=payment,
            audit=_audit_response(report),
        )

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/checkpoints/{phase}",
        response_model=EvidenceCheckpointResponse,
    )
    async def evidence_checkpoint(
        purchase_id: str, phase: ObserverPhase, authorization: str | None = Header(default=None)
    ) -> EvidenceCheckpointResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase evidence not found")
        return checkpoint_response(events, phase)

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/checkpoints/{phase}",
        response_model=EvidenceCheckpointResponse,
    )
    async def record_evidence_checkpoint(
        purchase_id: str,
        phase: ObserverPhase,
        body: EvidenceCheckpointRecordRequest,
        authorization: str | None = Header(default=None),
    ) -> EvidenceCheckpointResponse:
        require_internal(authorization)
        if container.checkpoint_mode != body.mode:
            raise HTTPException(
                status_code=409, detail="checkpoint mode does not match server mode"
            )
        if body.mode == "live" and (
            body.transaction_hash is None or body.chain_id is None or body.contract_address is None
        ):
            raise HTTPException(status_code=422, detail="live checkpoint proof is incomplete")
        if body.mode == "mock" and any(
            value is not None
            for value in (body.transaction_hash, body.chain_id, body.contract_address)
        ):
            raise HTTPException(
                status_code=422, detail="mock checkpoint must not claim chain proof"
            )
        events = await verified_events(purchase_id)
        response = checkpoint_response(events, phase)
        if (
            body.event_count != response.event_count
            or body.head_event_hash != response.head_event_hash
        ):
            raise HTTPException(
                status_code=409, detail="checkpoint proof targets a different evidence prefix"
            )
        if response.record is not None:
            existing = response.record.model_dump(exclude_none=True)
            requested = CheckpointRecordResponse(
                mode=body.mode,
                eventCount=body.event_count,
                headEventHash=body.head_event_hash,
                transactionHash=body.transaction_hash,
                chainId=body.chain_id,
                contractAddress=body.contract_address,
            ).model_dump(exclude_none=True)
            if existing != requested:
                raise HTTPException(
                    status_code=409, detail="different checkpoint proof already recorded"
                )
            return response
        try:
            await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.CHECKPOINT_RECORDED,
                occurred_at=container.clock.now(),
                actor={"id": "evidence-anchor-gateway", "type": "service"},
                payload={
                    "phase": phase.value,
                    "mode": body.mode,
                    "eventCount": body.event_count,
                    "headEventHash": body.head_event_hash,
                    "transactionHash": body.transaction_hash,
                    "chainId": body.chain_id,
                    "contractAddress": body.contract_address,
                },
                evidence_refs=(body.head_event_hash,),
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
        except EvidenceTransitionError:
            raced = checkpoint_response(await verified_events(purchase_id), phase)
            if raced.record is None:
                raise HTTPException(
                    status_code=409, detail="checkpoint record append conflicted"
                ) from None
            existing = raced.record.model_dump(exclude_none=True, by_alias=False)
            requested = CheckpointRecordResponse(
                mode=body.mode,
                eventCount=body.event_count,
                headEventHash=body.head_event_hash,
                transactionHash=body.transaction_hash,
                chainId=body.chain_id,
                contractAddress=body.contract_address,
            ).model_dump(exclude_none=True, by_alias=False)
            if existing != requested:
                raise HTTPException(
                    status_code=409, detail="different checkpoint proof already recorded"
                ) from None
            return raced
        return checkpoint_response(await verified_events(purchase_id), phase)

    @app.get("/purchases/{purchase_id}/events", response_model=list[EventResponse])
    async def list_events(purchase_id: str) -> list[EventResponse]:
        owner = require_owner()
        events = await verified_events(purchase_id)
        if not events or events[0].actor.get("id") != owner.lower():
            raise HTTPException(status_code=404)
        return [_event_response(event) for event in events]

    @app.post(
        "/purchases/{purchase_id}/sensitive/{payload_id}/access",
        response_model=SensitivePayloadResponse,
        status_code=201,
    )
    async def access_sensitive(purchase_id: str, payload_id: str) -> SensitivePayloadResponse:
        """H4: reading sensitive material is an explicit mutation, never a GET.

        Every read of an encrypted payload must leave an append-only access record, so
        this operation is a POST and the GET/read contract stays strictly zero-write.
        """
        owner = require_owner()
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
