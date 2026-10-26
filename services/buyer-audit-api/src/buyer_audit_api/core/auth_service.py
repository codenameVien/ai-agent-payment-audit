from __future__ import annotations

from eth_utils import is_address, to_checksum_address

from buyer_audit_api.core.errors import AuthenticationError
from buyer_audit_api.core.ports import Clock, EvidenceRepository


class AuthService:
    """The single buyer wallet the local demo pays from.

    The stack registers the payment executor's address once at startup; the evidence
    repository keeps exactly one buyer wallet and refuses a different one.
    """

    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def bind_buyer_wallet(self, *, buyer_wallet_address: str) -> str:
        if not is_address(buyer_wallet_address):
            raise AuthenticationError("invalid buyer wallet address")
        binding = await self._repository.bind_buyer_wallet(
            buyer_wallet_address=to_checksum_address(buyer_wallet_address).lower(),
            bound_at=self._clock.now(),
        )
        return binding.buyer_wallet_address

    async def get_buyer_wallet(self) -> str | None:
        binding = await self._repository.get_wallet_binding()
        return binding.buyer_wallet_address if binding else None
