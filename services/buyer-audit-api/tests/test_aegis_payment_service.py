"""Wallet budget policy and the authorize transition, driven by a real AEGIS reservation.

Ported from the superseded seller-quote payment tests: the budget guards, the per-token
wallet policy and the authorization idempotency are shared by the AEGIS runtime, so they
are exercised here against a real `aa-three-factor-v1` decision instead of a quote.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fakes.memory_repository import InMemoryEvidenceRepository

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.core.aegis_payment import AegisPaymentService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import PaymentConflictError, PaymentPolicyError
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentIntentState, PaymentService, WalletPolicy
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


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


async def decided_purchase(
    repository: InMemoryEvidenceRepository,
    clock: FrozenClock,
    *,
    policy: dict[str, object] | None = None,
) -> tuple[str, int]:
    """A real decision; returns its purchase id and the decided amount."""
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
        policy={"scoringPolicyVersion": "aa-three-factor-v1", **(policy or {})},
    )
    decision = await AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS", symbol="AEGIS", decimals=6, address=TOKEN, chain_id=84532
        ),
    ).decide(purchase_id=created.purchase_id)
    return created.purchase_id, decision.amount_units


async def bind_and_configure(
    repository: InMemoryEvidenceRepository,
    clock: FrozenClock,
    *,
    per_transaction_limit_units: int = 100_000,
    daily_limit_units: int = 1_000_000,
    spent_units: int = 0,
) -> None:
    await repository.bind_buyer_wallet(
        owner_address=OWNER, buyer_wallet_address=BUYER, bound_at=clock.now()
    )
    await repository.put_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER,
            policy_date=clock.now().date().isoformat(),
            token=TOKEN,
            per_transaction_limit_units=per_transaction_limit_units,
            daily_limit_units=daily_limit_units,
            spent_units=spent_units,
        )
    )


async def test_wallet_policies_for_two_tokens_can_coexist_on_same_day() -> None:
    clock = FrozenClock()
    repository = InMemoryEvidenceRepository()
    earlier = WalletPolicy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
        token="0x0000000000000000000000000000000000000003",
        per_transaction_limit_units=200_000,
        daily_limit_units=1_000_000,
        spent_units=100_000,
    )
    current = WalletPolicy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
        token=TOKEN,
        per_transaction_limit_units=250_000,
        daily_limit_units=1_000_000,
    )

    await repository.put_wallet_policy(earlier)
    await repository.put_wallet_policy(current)

    assert await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=earlier.policy_date, token=earlier.token
    ) == earlier
    assert await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=current.policy_date, token=TOKEN
    ) == current
    assert await repository.get_latest_wallet_policy(BUYER) == current


async def test_authorize_is_idempotent_and_a_different_authorization_conflicts() -> None:
    clock = FrozenClock()
    repository = InMemoryEvidenceRepository()
    purchase_id, amount_units = await decided_purchase(repository, clock)
    await bind_and_configure(repository, clock)
    _, claimed = await AegisPaymentService(repository=repository, clock=clock).reserve(
        purchase_id
    )
    payments = PaymentService(repository=repository, clock=clock)

    authorized = await asyncio.gather(
        *(
            payments.authorize(
                purchase_id=purchase_id,
                authorization_hash="0x" + "ab" * 32,
                signature="0xsecret-signature",
            )
            for _ in range(12)
        )
    )

    assert all(item.state is PaymentIntentState.AUTHORIZED for item in authorized)
    assert {item.authorization_nonce for item in authorized} == {claimed.authorization_nonce}
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.PAYMENT_AUTHORIZED) == 1
    authorization = next(event for event in events if event.type == EventType.PAYMENT_AUTHORIZED)
    # The signature is bound by hash; the raw value never enters append-only evidence.
    assert "signature" not in authorization.payload
    assert authorization.payload["signatureHash"].startswith("sha256:")
    assert authorization.payload["authorizationNonce"] == claimed.authorization_nonce
    assert "0xsecret-signature" not in str(events)
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
    )
    assert policy is not None
    assert policy.reserved_units == amount_units
    assert policy.spent_units == 0

    for authorization_hash, signature in (
        ("0x" + "ab" * 32, "0xother-signature"),
        ("0x" + "cd" * 32, "0xsecret-signature"),
    ):
        with pytest.raises(PaymentConflictError, match="different authorization"):
            await payments.authorize(
                purchase_id=purchase_id,
                authorization_hash=authorization_hash,
                signature=signature,
            )
    assert len(await repository.list_events(purchase_id)) == len(events)


async def test_a_parked_attempt_cannot_be_authorized_again() -> None:
    clock = FrozenClock()
    repository = InMemoryEvidenceRepository()
    purchase_id, amount_units = await decided_purchase(repository, clock)
    await bind_and_configure(repository, clock)
    await AegisPaymentService(repository=repository, clock=clock).reserve(purchase_id)
    payments = PaymentService(repository=repository, clock=clock)
    await payments.authorize(
        purchase_id=purchase_id, authorization_hash="0x" + "ab" * 32, signature="0xsig"
    )

    parked = await payments.require_reconciliation(
        purchase_id=purchase_id, reason="facilitator outcome unknown"
    )
    again = await payments.require_reconciliation(
        purchase_id=purchase_id, reason="retried by the executor"
    )

    assert parked.state is PaymentIntentState.RECONCILIATION_REQUIRED
    assert again == parked
    with pytest.raises(PaymentConflictError, match="cannot be authorized"):
        await payments.authorize(
            purchase_id=purchase_id, authorization_hash="0x" + "cd" * 32, signature="0xnew"
        )
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.PAYMENT_RECONCILIATION_REQUIRED) == 1
    # An unknown outcome keeps the budget reserved; it is never released for a re-payment.
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
    )
    assert policy is not None
    assert (policy.reserved_units, policy.spent_units) == (amount_units, 0)


@pytest.mark.parametrize(
    ("limit", "match"),
    [
        ("wallet_transaction", "wallet transaction limit"),
        ("wallet_daily", "wallet daily limit"),
        ("request_transaction", "request transaction limit"),
    ],
)
async def test_reserve_rejects_every_spending_limit_without_reserving(
    limit: str, match: str
) -> None:
    clock = FrozenClock()
    repository = InMemoryEvidenceRepository()
    probe_repository = InMemoryEvidenceRepository()
    _, amount_units = await decided_purchase(probe_repository, clock)
    purchase_id, decided_amount = await decided_purchase(
        repository,
        clock,
        policy=(
            {"maxTransactionUnits": amount_units - 1}
            if limit == "request_transaction"
            else None
        ),
    )
    assert decided_amount == amount_units
    if limit == "wallet_transaction":
        await bind_and_configure(repository, clock, per_transaction_limit_units=amount_units - 1)
    elif limit == "wallet_daily":
        # Already-spent units count against the day, so one more unit breaks the limit.
        await bind_and_configure(
            repository,
            clock,
            per_transaction_limit_units=amount_units,
            daily_limit_units=amount_units,
            spent_units=1,
        )
    else:
        await bind_and_configure(repository, clock)
    before = await repository.list_events(purchase_id)

    with pytest.raises(PaymentPolicyError, match=match):
        await AegisPaymentService(repository=repository, clock=clock).reserve(purchase_id)

    assert await repository.get_payment_intent(purchase_id) is None
    assert await repository.list_events(purchase_id) == before
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
    )
    assert policy is not None
    assert policy.reserved_units == 0


async def test_reserve_at_exactly_every_limit_succeeds() -> None:
    clock = FrozenClock()
    probe_repository = InMemoryEvidenceRepository()
    _, amount_units = await decided_purchase(probe_repository, clock)
    repository = InMemoryEvidenceRepository()
    purchase_id, _ = await decided_purchase(
        repository, clock, policy={"maxTransactionUnits": amount_units}
    )
    await bind_and_configure(
        repository,
        clock,
        per_transaction_limit_units=amount_units,
        daily_limit_units=amount_units,
    )

    _, intent = await AegisPaymentService(repository=repository, clock=clock).reserve(
        purchase_id
    )

    assert intent.state is PaymentIntentState.CLAIMED
    assert intent.amount_units == amount_units
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
    )
    assert policy is not None
    assert policy.reserved_units == amount_units
