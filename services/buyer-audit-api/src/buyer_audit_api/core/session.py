from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from buyer_audit_api.core.errors import AuthenticationError, ConfigurationError


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True, slots=True)
class SessionClaims:
    owner_address: str
    issued_at: datetime
    expires_at: datetime


class SessionCodec:
    def __init__(self, secret: bytes, ttl: timedelta = timedelta(hours=8)) -> None:
        if len(secret) < 32:
            raise ConfigurationError("session secret must be at least 32 bytes")
        self._secret = secret
        self._ttl = ttl

    def encode(self, owner_address: str, now: datetime) -> str:
        issued = int(now.astimezone(UTC).timestamp())
        payload = {
            "exp": issued + int(self._ttl.total_seconds()),
            "iat": issued,
            "jti": secrets.token_hex(16),
            "sub": owner_address.lower(),
        }
        body = _b64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64url_encode(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def decode(self, token: str, now: datetime) -> SessionClaims:
        try:
            body, provided = token.split(".", maxsplit=1)
            expected = _b64url_encode(
                hmac.new(self._secret, body.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(provided, expected):
                raise AuthenticationError("invalid session signature")
            raw: dict[str, Any] = json.loads(_b64url_decode(body))
            issued = datetime.fromtimestamp(int(raw["iat"]), tz=UTC)
            expires = datetime.fromtimestamp(int(raw["exp"]), tz=UTC)
            owner = str(raw["sub"])
        except AuthenticationError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AuthenticationError("malformed session") from exc
        if now.astimezone(UTC) >= expires:
            raise AuthenticationError("expired session")
        return SessionClaims(owner_address=owner, issued_at=issued, expires_at=expires)
