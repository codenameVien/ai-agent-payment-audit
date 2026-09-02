from __future__ import annotations

from datetime import timedelta

import pytest

from buyer_audit_api.core.errors import AuthenticationError
from buyer_audit_api.core.session import SessionCodec


def test_session_rejects_signature_tampering(clock) -> None:
    codec = SessionCodec(b"s" * 32)
    token = codec.encode("0xabc", clock.now())
    replacement = "a" if token[-1] != "a" else "b"

    with pytest.raises(AuthenticationError, match="signature"):
        codec.decode(token[:-1] + replacement, clock.now())


def test_session_rejects_expiry(clock) -> None:
    codec = SessionCodec(b"s" * 32, ttl=timedelta(minutes=5))
    token = codec.encode("0xabc", clock.now())

    with pytest.raises(AuthenticationError, match="expired"):
        codec.decode(token, clock.now() + timedelta(minutes=5))
