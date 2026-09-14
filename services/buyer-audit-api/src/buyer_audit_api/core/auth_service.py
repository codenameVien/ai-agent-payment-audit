from __future__ import annotations

from eth_utils import is_address, to_checksum_address

from buyer_audit_api.core.errors import AuthenticationError
from buyer_audit_api.core.ports import Clock, EvidenceRepository


class AuthService:
    """Owner-to-buyer-wallet binding for the single-user local demo.

    The owner identity is server configuration, so this service never issues a
    challenge or a session; it only validates addresses and delegates the binding to
    the evidence repository, which keeps one buyer wallet per owner.
    """

    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def bind_buyer_wallet(
        self,
        *,
        owner_address: str,
        buyer_wallet_address: str,
    ) -> str:
        if not is_address(owner_address):
            raise AuthenticationError("invalid owner wallet address")
        if not is_address(buyer_wallet_address):
            raise AuthenticationError("invalid buyer wallet address")
        binding = await self._repository.bind_buyer_wallet(
            owner_address=to_checksum_address(owner_address).lower(),
            buyer_wallet_address=to_checksum_address(buyer_wallet_address).lower(),
            bound_at=self._clock.now(),
        )
        return binding.buyer_wallet_address

    async def get_buyer_wallet(self, owner_address: str) -> str | None:
        binding = await self._repository.get_wallet_binding(owner_address)
        return binding.buyer_wallet_address if binding else None
