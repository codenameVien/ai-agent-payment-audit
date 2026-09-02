from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from eth_utils import is_address, to_checksum_address

from buyer_audit_api.core.errors import AuthenticationError
from buyer_audit_api.core.hashing import utc_iso
from buyer_audit_api.core.models import SiweChallenge
from buyer_audit_api.core.ports import Clock, EvidenceRepository, SiweSignatureVerifier
from buyer_audit_api.core.session import SessionCodec


def nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


class AuthService:
    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        verifier: SiweSignatureVerifier,
        session_codec: SessionCodec,
        clock: Clock,
        domain: str,
        uri: str,
        chain_id: int,
        challenge_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self._repository = repository
        self._verifier = verifier
        self._session_codec = session_codec
        self._clock = clock
        self._domain = domain
        self._uri = uri
        self._chain_id = chain_id
        self._challenge_ttl = challenge_ttl

    async def create_challenge(self, owner_address: str) -> SiweChallenge:
        if not is_address(owner_address):
            raise AuthenticationError("invalid Ethereum address")
        checksum_address = to_checksum_address(owner_address)
        now = self._clock.now()
        expires_at = now + self._challenge_ttl
        nonce = secrets.token_hex(12)
        message = (
            f"{self._domain} wants you to sign in with your Ethereum account:\n"
            f"{checksum_address}\n\n"
            "Sign in to the AI agent payment audit dashboard.\n\n"
            f"URI: {self._uri}\n"
            "Version: 1\n"
            f"Chain ID: {self._chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {utc_iso(now)}\n"
            f"Expiration Time: {utc_iso(expires_at)}"
        )
        await self._repository.issue_siwe_nonce(
            owner_address=checksum_address.lower(),
            nonce_hash=nonce_hash(nonce),
            expires_at=expires_at,
        )
        return SiweChallenge(
            owner_address=checksum_address,
            nonce=nonce,
            message=message,
            expires_at=expires_at,
        )

    async def verify_and_create_session(self, *, message: str, signature: str) -> str:
        now = self._clock.now()
        identity = self._verifier.verify(
            message=message,
            signature=signature,
            expected_domain=self._domain,
            expected_uri=self._uri,
            expected_chain_id=self._chain_id,
            now=now,
        )
        consumed = await self._repository.consume_siwe_nonce(
            owner_address=identity.owner_address.lower(),
            nonce_hash=nonce_hash(identity.nonce),
            consumed_at=now,
        )
        if not consumed:
            raise AuthenticationError("nonce expired, replaced, or already consumed")
        return self._session_codec.encode(identity.owner_address, now)

    def read_session(self, token: str) -> str:
        return self._session_codec.decode(token, self._clock.now()).owner_address

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
