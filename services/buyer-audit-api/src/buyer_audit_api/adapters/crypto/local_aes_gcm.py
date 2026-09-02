from __future__ import annotations

import base64
import json
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from buyer_audit_api.core.errors import ConfigurationError, EvidenceIntegrityError
from buyer_audit_api.core.hashing import canonical_bytes, sha256_bytes
from buyer_audit_api.core.models import JsonObject, SensitivePayload


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


class LocalEnvelopeCipher:
    def __init__(self, *, master_key: bytes, key_version: str = "local-v1") -> None:
        if len(master_key) != 32:
            raise ConfigurationError("payload master key must be exactly 32 bytes")
        self._master_key = master_key
        self._key_version = key_version

    def encrypt_json(
        self,
        *,
        purchase_id: str,
        kind: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> SensitivePayload:
        plaintext = canonical_bytes(dict(payload))
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        aad = f"{purchase_id}:{kind}".encode()
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)

        dek_nonce = os.urandom(12)
        dek_aad = f"payload-dek:{self._key_version}".encode()
        encrypted_dek = AESGCM(self._master_key).encrypt(dek_nonce, data_key, dek_aad)

        return SensitivePayload(
            payload_id=str(uuid.uuid4()),
            purchase_id=purchase_id,
            kind=kind,
            algorithm="AES-256-GCM+envelope",
            ciphertext=_encode(ciphertext),
            nonce=_encode(nonce),
            encrypted_dek=_encode(encrypted_dek),
            dek_nonce=_encode(dek_nonce),
            key_version=self._key_version,
            content_hash=sha256_bytes(plaintext),
            created_at=created_at,
        )

    def decrypt_json(self, payload: SensitivePayload) -> JsonObject:
        dek_aad = f"payload-dek:{payload.key_version}".encode()
        data_key = AESGCM(self._master_key).decrypt(
            _decode(payload.dek_nonce),
            _decode(payload.encrypted_dek),
            dek_aad,
        )
        plaintext = AESGCM(data_key).decrypt(
            _decode(payload.nonce),
            _decode(payload.ciphertext),
            f"{payload.purchase_id}:{payload.kind}".encode(),
        )
        if sha256_bytes(plaintext) != payload.content_hash:
            raise EvidenceIntegrityError("sensitive payload content hash mismatch")
        result = json.loads(plaintext)
        if not isinstance(result, dict):
            raise ValueError("decrypted payload is not a JSON object")
        return cast(JsonObject, result)
