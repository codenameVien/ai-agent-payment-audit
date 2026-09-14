from __future__ import annotations

import pytest
from eth_account import Account


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
