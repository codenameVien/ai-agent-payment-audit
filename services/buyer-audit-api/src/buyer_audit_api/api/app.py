from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import cast

import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from buyer_audit_api.api.schemas import (
    AuditAlertResponse,
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
    InternalPaymentAuthorizeRequest,
    InternalPaymentClaimRequest,
    InternalPaymentFailureRequest,
    InternalPaymentReconciliationRequest,
    InternalPaymentSettlementRequest,
    InternalPaymentTransactionBindingRequest,
    MeResponse,
    PaymentIntentResponse,
    PaymentQuoteViewResponse,
    PaymentViewResponse,
    PurchaseDetailResponse,
    PurchaseRequest,
    PurchaseResponse,
    PurchaseRunResponse,
    PurchaseSummaryResponse,
    ReputationIntentResponse,
    ReputationRecordRequest,
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
)
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.audit import AuditReport, AuditRepository, AuditService
from buyer_audit_api.core.errors import (
    AuthenticationError,
    DomainNotRegisteredError,
    EvidenceIntegrityError,
    EvidenceTransitionError,
    PaymentConflictError,
    PaymentEvidenceError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType, EvidenceEvent
from buyer_audit_api.core.payment import (
    PaymentIntent,
    PaymentRepository,
    PaymentService,
    PaymentView,
    WalletPolicy,
)
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState
from buyer_audit_api.domains.ai_inference.models import NormalizedAiRequest

SESSION_COOKIE = "pbl_session"


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


def _event_response(event: EvidenceEvent) -> EventResponse:
    payload, redacted = _redact(event.payload)
    assert isinstance(payload, dict)
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
    )


def _audit_response(report: AuditReport) -> AuditReportResponse:
    return AuditReportResponse(
        report_id=report.report_id,
        purchase_id=report.purchase_id,
        severity=report.severity.value,
        findings=[
            AuditFindingResponse(
                code=item.code,
                severity=item.severity.value,
                title=item.title,
                detail=item.detail,
                evidence_refs=list(item.evidence_refs),
                authority=item.authority,
            )
            for item in report.findings
        ],
        evidence_head_event_hash=report.evidence_head_event_hash,
        audit_bundle_hash=report.audit_bundle_hash,
    )


def _summary(events: list[EvidenceEvent]) -> PurchaseSummaryResponse:
    requested = events[0]
    by_type = {event.type: event for event in events}
    claimed = by_type.get(EventType.PAYMENT_INTENT_CLAIMED)
    settled = by_type.get(EventType.PAYMENT_SETTLED)
    audited = by_type.get(EventType.AUDITED)
    lifecycle = [
        event
        for event in events
        if event.type
        not in {
            EventType.SENSITIVE_PAYLOAD_ACCESSED,
            EventType.CORRECTION_RECORDED,
            EventType.EVIDENCE_ANCHORED,
        }
    ]
    findings = audited.payload.get("findings", []) if audited is not None else []
    return PurchaseSummaryResponse(
        purchase_id=requested.purchase_id,
        created_at=requested.occurred_at,
        domain=str(requested.payload.get("domain", "unknown")),
        request_summary=(
            dict(requested.payload["normalizedRequest"])
            if isinstance(requested.payload.get("normalizedRequest"), dict)
            else {}
        ),
        status=lifecycle[-1].type.value,
        amount_units=(
            int(claimed.payload["amountUnits"])
            if claimed is not None and isinstance(claimed.payload.get("amountUnits"), int)
            else None
        ),
        token=(
            str(claimed.payload["token"])
            if claimed is not None and isinstance(claimed.payload.get("token"), str)
            else None
        ),
        transaction_hash=(
            str(settled.payload["transactionHash"])
            if settled is not None and isinstance(settled.payload.get("transactionHash"), str)
            else None
        ),
        audit_severity=(str(audited.payload.get("severity")) if audited is not None else None),
        finding_count=len(findings) if isinstance(findings, list) else 0,
    )


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
    )


def create_app(container: AppContainer) -> FastAPI:
    payment_service = PaymentService(
        repository=cast(PaymentRepository, container.repository),
        clock=container.clock,
    )
    audit_service = AuditService(
        repository=cast(AuditRepository, container.repository),
        clock=container.clock,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await container.repository.ensure_indexes()
        yield
        await container.repository.close()

    app = FastAPI(title="Buyer & Audit API", version="0.1.0", lifespan=lifespan)

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
        return {"status": "ok"}

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

    @app.post(
        "/internal/evidence/purchases/{purchase_id}/reputation",
        response_model=EventResponse,
    )
    async def record_reputation(
        purchase_id: str,
        body: ReputationRecordRequest,
        authorization: str | None = Header(default=None),
    ) -> EventResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        if not events:
            raise HTTPException(status_code=404, detail="purchase not found")
        existing = [event for event in events if event.type == EventType.REPUTATION_RECORDED]
        audit = next((event for event in events if event.type == EventType.AUDITED), None)
        if audit is None:
            raise HTTPException(status_code=409, detail="reputation requires audit")
        expected_feedback_hash = "0x" + str(audit.payload.get("auditBundleHash", "")).split(":")[-1]
        if body.feedback_hash.lower() != expected_feedback_hash.lower():
            raise HTTPException(status_code=409, detail="feedback hash does not bind audit bundle")
        has_settlement = any(event.type == EventType.PAYMENT_SETTLED for event in events)
        has_failure = any(event.type == EventType.PAYMENT_FAILED for event in events)
        expected_value = 100 if has_settlement else 0 if has_failure else None
        if expected_value is None or body.objective_value != expected_value:
            raise HTTPException(status_code=409, detail="objective feedback outcome mismatch")
        quoted = next((event for event in events if event.type == EventType.QUOTED), None)
        decided = next((event for event in events if event.type == EventType.DECIDED), None)
        winner = decided.payload.get("winner", {}) if decided is not None else {}
        winner_quote_id = winner.get("quote_id") if isinstance(winner, dict) else None
        identities = quoted.payload.get("quoteIdentityEvidence", []) if quoted else []
        selected_identity = next(
            (
                item
                for item in identities
                if isinstance(item, dict) and item.get("quoteId") == winner_quote_id
            ),
            None,
        )
        if (
            selected_identity is None
            or str(selected_identity.get("erc8004AgentId")) != body.erc8004_agent_id
        ):
            raise HTTPException(status_code=409, detail="feedback agent identity mismatch")
        payload = {
            "chainId": body.chain_id,
            "erc8004AgentId": body.erc8004_agent_id,
            "feedbackHash": body.feedback_hash.lower(),
            "objectiveValue": body.objective_value,
            "registryAddress": body.registry_address.lower(),
            "transactionHash": body.transaction_hash.lower(),
        }
        if existing:
            if len(existing) == 1 and existing[0].payload == payload:
                return _event_response(existing[0])
            raise HTTPException(status_code=409, detail="different reputation already recorded")
        event = await container.repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.REPUTATION_RECORDED,
            occurred_at=container.clock.now(),
            actor={"id": "erc8004-reputation-writer", "type": "service"},
            payload=payload,
            evidence_refs=(audit.event_hash, body.feedback_hash, body.transaction_hash),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
        )
        return _event_response(event)

    @app.get(
        "/internal/evidence/purchases/{purchase_id}/reputation-intent",
        response_model=ReputationIntentResponse,
    )
    async def prepare_reputation(
        purchase_id: str,
        erc8004_agent_id: str,
        authorization: str | None = Header(default=None),
    ) -> ReputationIntentResponse:
        require_internal(authorization)
        events = await verified_events(purchase_id)
        audit = next((event for event in events if event.type == EventType.AUDITED), None)
        if audit is None:
            raise HTTPException(status_code=409, detail="reputation requires final audit")
        has_settlement = any(event.type == EventType.PAYMENT_SETTLED for event in events)
        has_failure = any(event.type == EventType.PAYMENT_FAILED for event in events)
        objective_value = 100 if has_settlement else 0 if has_failure else None
        if objective_value is None:
            raise HTTPException(status_code=409, detail="payment outcome is not final")
        quoted = next((event for event in events if event.type == EventType.QUOTED), None)
        decided = next((event for event in events if event.type == EventType.DECIDED), None)
        winner = decided.payload.get("winner", {}) if decided is not None else {}
        winner_quote_id = winner.get("quote_id") if isinstance(winner, dict) else None
        identities = quoted.payload.get("quoteIdentityEvidence", []) if quoted else []
        selected_identity = next(
            (
                item
                for item in identities
                if isinstance(item, dict) and item.get("quoteId") == winner_quote_id
            ),
            None,
        )
        if (
            selected_identity is None
            or str(selected_identity.get("erc8004AgentId")) != erc8004_agent_id
            or selected_identity.get("identityVerified") is not True
        ):
            raise HTTPException(status_code=409, detail="feedback agent identity mismatch")
        audit_hash = str(audit.payload.get("auditBundleHash", ""))
        digest = audit_hash.split(":")[-1]
        if len(digest) != 64:
            raise HTTPException(status_code=409, detail="audit bundle hash is malformed")
        existing = next(
            (event for event in events if event.type == EventType.REPUTATION_RECORDED),
            None,
        )
        existing_transaction: str | None = None
        if existing is not None:
            if (
                str(existing.payload.get("erc8004AgentId")) != erc8004_agent_id
                or int(existing.payload.get("objectiveValue", -1)) != objective_value
                or str(existing.payload.get("feedbackHash", "")).lower()
                != ("0x" + digest).lower()
            ):
                raise HTTPException(status_code=409, detail="existing reputation differs")
            raw_transaction = existing.payload.get("transactionHash")
            if not isinstance(raw_transaction, str):
                raise HTTPException(
                    status_code=409, detail="existing reputation transaction missing"
                )
            existing_transaction = raw_transaction
        return ReputationIntentResponse(
            erc8004_agent_id=erc8004_agent_id,
            objective_value=objective_value,
            feedback_hash="0x" + digest,
            transaction_hash=existing_transaction,
        )

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
        audit = _audit_response(await audit_service.audit(purchase_id))
        return PurchaseDetailResponse(
            summary=_summary(events),
            events=[_event_response(event) for event in events],
            audit=audit,
        )

    @app.get("/audit-alerts", response_model=list[AuditAlertResponse])
    async def audit_alerts(
        pbl_session: str | None = Cookie(default=None),
    ) -> list[AuditAlertResponse]:
        owner = require_owner(pbl_session)
        alerts: list[AuditAlertResponse] = []
        for events in await owner_purchase_events(owner):
            report = await audit_service.audit(events[0].purchase_id)
            alerts.extend(
                AuditAlertResponse(
                    purchase_id=report.purchase_id,
                    report_id=report.report_id,
                    code=finding.code,
                    severity=finding.severity.value,
                    title=finding.title,
                    detail=finding.detail,
                    evidence_refs=list(finding.evidence_refs),
                    authority=finding.authority,
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
                        str(reputation.payload["transactionHash"])
                        if reputation is not None
                        else None
                    ),
                )
        return sorted(agents.values(), key=lambda item: item.seller_agent_id)

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

    @app.put("/auth/buyer-wallet", response_model=BuyerWalletResponse)
    async def bind_buyer_wallet(
        body: BuyerWalletRequest,
        pbl_session: str | None = Cookie(default=None),
    ) -> BuyerWalletResponse:
        owner = require_owner(pbl_session)
        try:
            buyer_wallet = await container.auth_service.bind_buyer_wallet(
                owner_address=owner,
                buyer_wallet_address=body.buyer_wallet_address,
            )
        except (AuthenticationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return BuyerWalletResponse(
            owner_address=owner,
            buyer_wallet_address=buyer_wallet,
        )

    @app.post("/purchases", response_model=PurchaseResponse, status_code=201)
    async def create_purchase(
        body: PurchaseRequest,
        pbl_session: str | None = Cookie(default=None),
    ) -> PurchaseResponse:
        owner = require_owner(pbl_session)
        try:
            created = await container.purchase_service.create(
                owner_address=owner,
                domain_id=body.domain,
                raw_request=body.request,
                budget_units=body.budget_units,
                policy=body.policy,
            )
        except DomainNotRegisteredError as exc:
            raise HTTPException(status_code=422, detail=f"unknown domain: {exc}") from exc
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
            raise HTTPException(status_code=503, detail="commerce gateway unavailable")
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

    @app.get(
        "/purchases/{purchase_id}/sensitive/{payload_id}",
        response_model=SensitivePayloadResponse,
    )
    async def read_sensitive(
        purchase_id: str,
        payload_id: str,
        pbl_session: str | None = Cookie(default=None),
    ) -> SensitivePayloadResponse:
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
