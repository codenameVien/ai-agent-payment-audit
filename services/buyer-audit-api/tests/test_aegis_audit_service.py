"""Persisted-audit behaviour of `AuditService` on real AEGIS purchases.

Ported from the superseded Phase 6 audit tests: finding serialization, the semantic
advisory cap, reuse of a persisted audit, preview before a terminal outcome, fail-closed
re-audit and the append race are shared behaviour, so they run against purchases that
went through the actual `aa-three-factor-v1` decision and payment services.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from fakes.memory_repository import InMemoryEvidenceRepository

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.core.aegis_payment import AegisPaymentService
from buyer_audit_api.core.audit import (
    LEGACY_RULESET_VERSION,
    RULESET_VERSION,
    AuditAuthority,
    AuditEvaluator,
    AuditFinding,
    AuditReportReader,
    AuditRepository,
    AuditService,
    AuditSeverity,
    finding_from_payload,
    finding_payload,
    is_final_eligible,
)
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import EvidenceIntegrityError, EvidenceTransitionError
from buyer_audit_api.core.models import EventType, EvidenceEvent, EvidenceHead
from buyer_audit_api.core.payment import PaymentService, WalletPolicy
from buyer_audit_api.core.projections import AuditStatus, PurchaseProjectionService
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import AegisDecisionWorkflow

OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000000"
PROMPT = "x" * 100

EVALUATOR = AuditEvaluator()
READER = AuditReportReader()
PROJECTIONS = PurchaseProjectionService()


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


async def _decided(repository: InMemoryEvidenceRepository, clock: FrozenClock) -> str:
    purchases = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry([AiInferenceDomainModule(default_max_output_tokens=1024)]),
        clock=clock,
    )
    created = await purchases.create(
        owner_address=OWNER,
        domain_id="ai_inference",
        raw_request={"requestSchema": "aegis-aa-v1", "prompt": PROMPT},
        budget_units=5_000,
        policy={"scoringPolicyVersion": "aa-three-factor-v1"},
    )
    await AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS", symbol="AEGIS", decimals=6, address=TOKEN, chain_id=84532
        ),
    ).decide(purchase_id=created.purchase_id)
    return created.purchase_id


async def _authorized(repository: InMemoryEvidenceRepository, clock: FrozenClock) -> str:
    purchase_id = await _decided(repository, clock)
    await repository.bind_buyer_wallet(
        owner_address=OWNER, buyer_wallet_address=BUYER, bound_at=clock.now()
    )
    await repository.put_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER,
            policy_date=clock.now().date().isoformat(),
            token=TOKEN,
            per_transaction_limit_units=100_000,
            daily_limit_units=1_000_000,
        )
    )
    await AegisPaymentService(repository=repository, clock=clock).reserve(purchase_id)
    await PaymentService(repository=repository, clock=clock).authorize(
        purchase_id=purchase_id, authorization_hash="0x" + "ab" * 32, signature="0xsig"
    )
    return purchase_id


async def settled_delivered() -> tuple[InMemoryEvidenceRepository, FrozenClock, str]:
    """Paid through the real services and delivered: the audit may be persisted."""
    repository, clock = InMemoryEvidenceRepository(), FrozenClock()
    purchase_id = await _authorized(repository, clock)
    aegis = AegisPaymentService(repository=repository, clock=clock)
    settled = await aegis.settle(
        purchase_id=purchase_id,
        facilitator_transaction="x402mock:" + "1" * 32,
        facilitator_network="eip155:84532",
        facilitator_payer=BUYER,
    )
    terms = await aegis.terms(purchase_id)
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.DELIVERED,
        occurred_at=clock.now(),
        actor={"id": f"{terms.provider_id}-provider-gateway", "type": "service"},
        payload={
            "executionMode": "mock",
            "modelId": terms.provider_model_id,
            "modelVersion": terms.model_version,
            "observedExecutionMs": 3,
            "providerId": terms.provider_id,
            "responseHash": "sha256:" + "2" * 64,
            "responseId": "sha256:" + "1" * 64,
            "termsBindingHash": settled.quote_id,
        },
        evidence_refs=(settled.quote_id,),
    )
    return repository, clock, purchase_id


def rule_ids(findings: tuple[AuditFinding, ...]) -> set[str]:
    return {item.rule_id for item in findings}


class UncertainAdvisor:
    async def advise(self, **_kwargs: object) -> tuple[AuditFinding, ...]:
        return (
            AuditFinding(
                rule_id="SEM-REQUEST-RATIONALE-UNCERTAIN",
                severity=AuditSeverity.CAUTION,
                title="advisory",
                detail="the stored rationale is ambiguous",
                authority=AuditAuthority.SEMANTIC_ADVISORY,
            ),
        )


class OverreachingAdvisor:
    async def advise(self, **_kwargs: object) -> tuple[AuditFinding, ...]:
        return (
            AuditFinding(
                rule_id="SEM-FORCED-RISK",
                severity=AuditSeverity.RISK,
                title="advisory",
                detail="semantic analysis cannot own the verdict",
                authority=AuditAuthority.SEMANTIC_ADVISORY,
            ),
        )


class NormalAssertingAdvisor:
    async def advise(self, **_kwargs: object) -> tuple[AuditFinding, ...]:
        return (
            AuditFinding(
                rule_id="SEM-NORMAL",
                severity=AuditSeverity.NORMAL,
                title="invalid",
                detail="semantic analysis cannot assert normal authority",
                authority=AuditAuthority.SEMANTIC_ADVISORY,
            ),
        )


# --------------------------------------------------------------------------------------
# Finding serialization
# --------------------------------------------------------------------------------------


def test_structured_findings_serialize_every_canonical_field() -> None:
    finding = AuditFinding(
        rule_id="AUD-BUDGET-EXCEEDED",
        severity=AuditSeverity.RISK,
        title="title",
        detail="detail",
        evidence_refs=("sha256:" + "aa" * 32,),
        expected={"maxAmountUnits": 1_000},
        observed={"amountUnits": 2_000},
        mismatched_fields=("amount",),
    )

    payload = finding_payload(finding)

    assert payload["ruleId"] == "AUD-BUDGET-EXCEEDED"
    assert payload["code"] == payload["ruleId"]
    assert payload["rulesetVersion"] == RULESET_VERSION
    assert payload["authority"] == AuditAuthority.DETERMINISTIC.value
    assert payload["expected"] == {"maxAmountUnits": 1_000}
    assert payload["observed"] == {"amountUnits": 2_000}
    assert payload["mismatchedFields"] == ["amount"]
    assert finding_from_payload(payload) == finding


def test_older_finding_documents_still_parse_without_rewriting() -> None:
    older = {
        "authority": "deterministic",
        "code": "AUD-PAYMENT-PENDING",
        "detail": "older detail",
        "evidenceRefs": ["sha256:" + "bb" * 32],
        "severity": "CAUTION",
        "title": "older title",
    }

    parsed = finding_from_payload(older)

    assert parsed.rule_id == "AUD-PAYMENT-PENDING"
    assert parsed.code == "AUD-PAYMENT-PENDING"
    assert parsed.authority is AuditAuthority.DETERMINISTIC
    assert parsed.ruleset_version == LEGACY_RULESET_VERSION
    assert parsed.expected is None
    assert parsed.mismatched_fields == ()


def test_older_semantic_authority_maps_to_the_advisory_enum() -> None:
    parsed = finding_from_payload(
        {
            "authority": "semantic",
            "code": "SEM-REQUEST-RATIONALE-UNCERTAIN",
            "detail": "advisory",
            "severity": "CAUTION",
            "title": "advisory",
        }
    )
    assert parsed.authority is AuditAuthority.SEMANTIC_ADVISORY


# --------------------------------------------------------------------------------------
# Semantic advisories never own the verdict
# --------------------------------------------------------------------------------------


async def test_semantic_only_advisory_caps_the_verdict_at_a_warning() -> None:
    repository, clock, purchase_id = await settled_delivered()
    service = AuditService(
        repository=repository, clock=clock, semantic_advisor=OverreachingAdvisor()
    )

    report = await service.audit(purchase_id)

    assert report.severity is AuditSeverity.CAUTION
    assert rule_ids(report.findings) == {"SEM-FORCED-RISK"}
    projection = PROJECTIONS.project(await repository.list_events(purchase_id))
    assert projection.audit_status is AuditStatus.AUDITED_WARNING


async def test_semantic_advisory_cannot_lower_a_deterministic_risk() -> None:
    """A failed purchase whose snapshot body is unreadable keeps its deterministic RISK."""
    source, clock = InMemoryEvidenceRepository(), FrozenClock()
    purchase_id = await _authorized(source, clock)
    await AegisPaymentService(repository=source, clock=clock).fail(
        purchase_id=purchase_id, reason="facilitator refused the payment"
    )
    # Replay the same chain into a store that never received the snapshot document, so
    # the deterministic recalculation has no independent input and reports a RISK.
    repository = InMemoryEvidenceRepository()
    for event in await source.list_events(purchase_id):
        await repository.append_event(
            purchase_id=purchase_id,
            event_type=event.type,
            occurred_at=event.occurred_at,
            actor=event.actor,
            payload=event.payload,
            evidence_refs=event.evidence_refs,
        )
    service = AuditService(
        repository=repository, clock=clock, semantic_advisor=UncertainAdvisor()
    )

    report = await service.audit(purchase_id)

    assert report.severity is AuditSeverity.RISK
    assert "AUD-AA-SNAPSHOT-BODY-UNAVAILABLE" in rule_ids(report.findings)
    assert "SEM-REQUEST-RATIONALE-UNCERTAIN" in rule_ids(report.findings)
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    assert PROJECTIONS.project(events).audit_status is AuditStatus.AUDITED_RISK


async def test_semantic_advisor_can_never_assert_a_normal_verdict() -> None:
    repository, clock, purchase_id = await settled_delivered()

    with pytest.raises(ValueError, match="only add caution/risk"):
        await AuditService(
            repository=repository, clock=clock, semantic_advisor=NormalAssertingAdvisor()
        ).audit(purchase_id)

    assert all(
        event.type != EventType.AUDITED for event in await repository.list_events(purchase_id)
    )


# --------------------------------------------------------------------------------------
# One persisted audit, reused; a pure evaluator
# --------------------------------------------------------------------------------------


async def test_concurrent_audits_persist_exactly_one_deterministic_report() -> None:
    repository, clock, purchase_id = await settled_delivered()
    audit = AuditService(repository=repository, clock=clock)

    reports = await asyncio.gather(*(audit.audit(purchase_id) for _ in range(12)))

    assert all(report.severity is AuditSeverity.NORMAL for report in reports)
    assert all(report.findings == () for report in reports)
    assert len({report.audit_bundle_hash for report in reports}) == 1
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1


async def test_persisted_audit_is_returned_by_the_reader_and_is_reused() -> None:
    repository, clock, purchase_id = await settled_delivered()
    service = AuditService(repository=repository, clock=clock)
    events_before = await repository.list_events(purchase_id)
    assert READER.persisted(events_before) is None

    first = await service.audit(purchase_id)
    events_after = await repository.list_events(purchase_id)
    again = await service.audit(purchase_id)

    assert READER.persisted(events_after) == first
    assert again == first
    assert len(events_after) == len(events_before) + 1
    assert len(await repository.list_events(purchase_id)) == len(events_after)
    assert first.ruleset_version == RULESET_VERSION


async def test_evaluator_is_pure_and_never_appends() -> None:
    repository, _, purchase_id = await settled_delivered()
    events = await repository.list_events(purchase_id)

    first = EVALUATOR.evaluate(events)
    second = EVALUATOR.evaluate(events)

    assert first == second
    assert first.final_eligible is True
    assert len(await repository.list_events(purchase_id)) == len(events)


@pytest.mark.parametrize("stage", ["authorized", "reconciliation"])
async def test_nonfinal_purchase_gets_a_preview_without_a_persisted_audit(stage: str) -> None:
    repository, clock = InMemoryEvidenceRepository(), FrozenClock()
    purchase_id = await _authorized(repository, clock)
    if stage == "reconciliation":
        await PaymentService(repository=repository, clock=clock).require_reconciliation(
            purchase_id=purchase_id, reason="facilitator outcome unknown"
        )
    service = AuditService(repository=repository, clock=clock)

    preview = await service.audit(purchase_id)

    assert preview.severity is AuditSeverity.CAUTION
    assert rule_ids(preview.findings) == {"AUD-PAYMENT-PENDING"}
    events = await repository.list_events(purchase_id)
    assert is_final_eligible(events) is False
    assert all(event.type != EventType.AUDITED for event in events)
    assert READER.persisted(events) is None
    assert PROJECTIONS.project(events).audit_status is AuditStatus.PENDING_AUDIT


# --------------------------------------------------------------------------------------
# A persisted audit is current only for its own head and ruleset
# --------------------------------------------------------------------------------------


async def test_reaudit_fails_closed_once_evidence_passes_the_audited_head() -> None:
    """H4: a changed head must demand a correction policy, never a silent stale report."""
    repository, clock, purchase_id = await settled_delivered()
    service = AuditService(repository=repository, clock=clock)
    first = await service.audit(purchase_id)
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.SENSITIVE_PAYLOAD_ACCESSED,
        occurred_at=clock.now(),
        actor={"id": OWNER, "type": "user"},
        payload={"kind": "request", "payloadId": "payload-1"},
    )

    with pytest.raises(EvidenceIntegrityError, match="advanced after the terminal audit"):
        await service.audit(purchase_id)

    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    persisted = READER.persisted_audit(events)
    assert persisted is not None
    assert persisted.report == first
    assert persisted.covers_head is False
    assert PROJECTIONS.project(events).audit_covers_head is False


async def test_persisted_audit_must_describe_the_head_it_was_appended_to() -> None:
    """H4: a stored audit whose head hash does not match its position is an integrity error."""
    repository, clock, purchase_id = await settled_delivered()
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.AUDITED,
        payload={
            "auditBundleHash": "sha256:" + "44" * 32,
            "evidenceHeadEventHash": "sha256:" + "55" * 32,
            "findings": [],
            "reportId": "audit:" + "66" * 32,
            "rulesetVersion": RULESET_VERSION,
            "severity": "NORMAL",
        },
        occurred_at=clock.now(),
        actor={"id": "deterministic-audit-engine", "type": "service"},
    )
    events = await repository.list_events(purchase_id)

    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        READER.persisted_audit(events)
    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        await AuditService(repository=repository, clock=clock).audit(purchase_id)


async def test_an_older_ruleset_audit_is_never_returned_as_current() -> None:
    """H4: P6-AC-03.5 requires the same head AND the same ruleset."""
    repository, clock, purchase_id = await settled_delivered()
    head = await repository.get_event_head(purchase_id)
    assert head is not None
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.AUDITED,
        occurred_at=clock.now(),
        actor={"id": "deterministic-audit-engine", "type": "service"},
        payload={
            "auditBundleHash": "sha256:" + "44" * 32,
            "evidenceHeadEventHash": head.head_event_hash,
            "findings": [],
            "reportId": "audit:" + "66" * 32,
            "rulesetVersion": "phase6.rules.old",
            "severity": "NORMAL",
        },
    )
    service = AuditService(repository=repository, clock=clock)

    with pytest.raises(EvidenceIntegrityError, match="re-audit policy is required"):
        await service.audit(purchase_id)

    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    persisted = READER.persisted_audit(events)
    assert persisted is not None
    assert persisted.report.ruleset_version == "phase6.rules.old"
    assert persisted.covers_head is True


# --------------------------------------------------------------------------------------
# The append race
# --------------------------------------------------------------------------------------


class RacingRepository:
    """Serves the pre-race snapshot, loses the append CAS, then serves the real state.

    This is the exact shape of a lost audit race: the loser evaluated an older head, its
    append is rejected, and only then does it see the winner's evidence.
    """

    def __init__(
        self,
        inner: InMemoryEvidenceRepository,
        before_events: list[EvidenceEvent],
        before_head: EvidenceHead,
    ) -> None:
        self._inner = inner
        self._before_events = before_events
        self._before_head = before_head
        self.append_attempts = 0

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]:
        if self.append_attempts == 0:
            return list(self._before_events)
        return await self._inner.list_events(purchase_id)

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None:
        if self.append_attempts == 0:
            return self._before_head
        return await self._inner.get_event_head(purchase_id)

    async def get_document(self, document_id: str) -> object:
        return await self._inner.get_document(document_id)

    async def append_event(self, **kwargs: object) -> EvidenceEvent:
        self.append_attempts += 1
        raise EvidenceTransitionError("append lost the evidence head race")


async def test_append_race_loser_never_returns_a_stale_audit() -> None:
    """H4: the race recovery path applies exactly the normal path's currency rules."""
    repository, clock, purchase_id = await settled_delivered()
    before_events = await repository.list_events(purchase_id)
    before_head = await repository.get_event_head(purchase_id)
    assert before_head is not None

    # The winner audits, then an unrelated append advances the head past that audit.
    await AuditService(repository=repository, clock=clock).audit(purchase_id)
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.SENSITIVE_PAYLOAD_ACCESSED,
        occurred_at=clock.now(),
        actor={"id": OWNER, "type": "user"},
        payload={"kind": "request", "payloadId": "payload-1"},
    )

    racing = RacingRepository(repository, before_events, before_head)
    loser = AuditService(repository=cast(AuditRepository, racing), clock=clock)

    with pytest.raises(EvidenceIntegrityError, match="advanced after the terminal audit"):
        await loser.audit(purchase_id)

    assert racing.append_attempts == 1
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    assert events[-1].type is EventType.SENSITIVE_PAYLOAD_ACCESSED


async def test_append_race_loser_returns_the_winner_report_when_it_is_current() -> None:
    """The recovery path still answers with the committed audit when it covers the head."""
    repository, clock, purchase_id = await settled_delivered()
    before_events = await repository.list_events(purchase_id)
    before_head = await repository.get_event_head(purchase_id)
    assert before_head is not None
    winner = await AuditService(repository=repository, clock=clock).audit(purchase_id)

    racing = RacingRepository(repository, before_events, before_head)
    loser = AuditService(repository=cast(AuditRepository, racing), clock=clock)

    assert await loser.audit(purchase_id) == winner
    assert racing.append_attempts == 1
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
