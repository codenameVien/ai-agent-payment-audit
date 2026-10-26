from __future__ import annotations

import pytest
from eth_account import Account


@pytest.mark.asyncio
async def test_only_one_buyer_wallet_can_be_bound(container) -> None:
    buyer = Account.create()

    assert await container.auth_service.get_buyer_wallet() is None
    assert (
        await container.auth_service.bind_buyer_wallet(buyer_wallet_address=buyer.address)
        == buyer.address.lower()
    )
    # Re-registering the same wallet at every stack start is harmless.
    assert (
        await container.auth_service.bind_buyer_wallet(buyer_wallet_address=buyer.address)
        == buyer.address.lower()
    )
    with pytest.raises(ValueError, match="different buyer wallet"):
        await container.auth_service.bind_buyer_wallet(
            buyer_wallet_address=Account.create().address,
        )
    assert await container.auth_service.get_buyer_wallet() == buyer.address.lower()
