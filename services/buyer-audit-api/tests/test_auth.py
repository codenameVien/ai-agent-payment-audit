from __future__ import annotations

from datetime import timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from buyer_audit_api.core.errors import AuthenticationError


def sign_message(account, message: str) -> str:
    return account.sign_message(encode_defunct(text=message)).signature.hex()


@pytest.mark.asyncio
async def test_siwe_nonce_is_consumed_exactly_once(container) -> None:
    account = Account.create()
    challenge = await container.auth_service.create_challenge(account.address)
    signature = sign_message(account, challenge.message)

    token = await container.auth_service.verify_and_create_session(
        message=challenge.message,
        signature=signature,
    )

    assert container.auth_service.read_session(token) == account.address.lower()
    with pytest.raises(AuthenticationError, match="already consumed"):
        await container.auth_service.verify_and_create_session(
            message=challenge.message,
            signature=signature,
        )


@pytest.mark.asyncio
async def test_wrong_chain_is_rejected_without_consuming_nonce(container) -> None:
    account = Account.create()
    challenge = await container.auth_service.create_challenge(account.address)
    wrong_chain_message = challenge.message.replace("Chain ID: 84532", "Chain ID: 1")

    with pytest.raises(AuthenticationError, match="chain"):
        await container.auth_service.verify_and_create_session(
            message=wrong_chain_message,
            signature=sign_message(account, wrong_chain_message),
        )

    token = await container.auth_service.verify_and_create_session(
        message=challenge.message,
        signature=sign_message(account, challenge.message),
    )
    assert container.auth_service.read_session(token) == account.address.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("localhost wants", "evil.example wants", "invalid SIWE"),
        ("URI: http://localhost:3000", "URI: https://evil.example", "URI mismatch"),
    ],
)
async def test_wrong_domain_or_uri_is_rejected(
    container,
    old: str,
    new: str,
    error: str,
) -> None:
    account = Account.create()
    challenge = await container.auth_service.create_challenge(account.address)
    tampered = challenge.message.replace(old, new)

    with pytest.raises(AuthenticationError, match=error):
        await container.auth_service.verify_and_create_session(
            message=tampered,
            signature=sign_message(account, tampered),
        )


@pytest.mark.asyncio
async def test_buyer_wallet_is_unique_across_owners(container) -> None:
    buyer = Account.create()
    first_owner = Account.create()
    second_owner = Account.create()

    assert (
        await container.auth_service.bind_buyer_wallet(
            owner_address=first_owner.address,
            buyer_wallet_address=buyer.address,
        )
        == buyer.address.lower()
    )
    with pytest.raises(ValueError, match="already bound"):
        await container.auth_service.bind_buyer_wallet(
            owner_address=second_owner.address,
            buyer_wallet_address=buyer.address,
        )
    with pytest.raises(ValueError, match="different buyer wallet"):
        await container.auth_service.bind_buyer_wallet(
            owner_address=first_owner.address,
            buyer_wallet_address=Account.create().address,
        )


@pytest.mark.asyncio
async def test_expired_challenge_is_rejected(container, clock) -> None:
    account = Account.create()
    challenge = await container.auth_service.create_challenge(account.address)
    clock.value += timedelta(minutes=6)

    with pytest.raises(AuthenticationError):
        await container.auth_service.verify_and_create_session(
            message=challenge.message,
            signature=sign_message(account, challenge.message),
        )


@pytest.mark.asyncio
async def test_invalid_signature_does_not_consume_nonce(container) -> None:
    owner = Account.create()
    attacker = Account.create()
    challenge = await container.auth_service.create_challenge(owner.address)

    with pytest.raises(AuthenticationError):
        await container.auth_service.verify_and_create_session(
            message=challenge.message,
            signature=sign_message(attacker, challenge.message),
        )

    token = await container.auth_service.verify_and_create_session(
        message=challenge.message,
        signature=sign_message(owner, challenge.message),
    )
    assert container.auth_service.read_session(token) == owner.address.lower()
