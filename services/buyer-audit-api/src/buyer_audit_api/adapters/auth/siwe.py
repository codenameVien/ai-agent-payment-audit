from __future__ import annotations

from datetime import datetime

import siwe  # type: ignore[import-untyped]
from eth_utils import to_checksum_address
from siwe import SiweMessage  # type: ignore[import-untyped]

from buyer_audit_api.core.errors import AuthenticationError
from buyer_audit_api.core.models import VerifiedIdentity


class PythonSiweVerifier:
    def verify(
        self,
        *,
        message: str,
        signature: str,
        expected_domain: str,
        expected_uri: str,
        expected_chain_id: int,
        now: datetime,
    ) -> VerifiedIdentity:
        try:
            parsed = SiweMessage.from_message(message=message)
            parsed.verify(
                signature=signature,
                domain=expected_domain,
                nonce=parsed.nonce,
                timestamp=now,
            )
        except (siwe.VerificationError, ValueError) as exc:
            raise AuthenticationError("invalid SIWE message or signature") from exc

        if str(parsed.uri) != expected_uri:
            raise AuthenticationError("SIWE URI mismatch")
        if int(parsed.chain_id) != expected_chain_id:
            raise AuthenticationError("SIWE chain mismatch")
        return VerifiedIdentity(
            owner_address=to_checksum_address(parsed.address),
            nonce=str(parsed.nonce),
        )
