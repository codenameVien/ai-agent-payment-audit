from __future__ import annotations

from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.core.errors import EvidenceIntegrityError


def test_envelope_encryption_round_trip_and_randomness(clock) -> None:
    cipher = LocalEnvelopeCipher(master_key=b"k" * 32)
    raw = {"prompt": "매우 민감한 요청", "nested": {"value": 7}}

    first = cipher.encrypt_json(
        purchase_id="purchase-1",
        kind="request",
        payload=raw,
        created_at=clock.now(),
    )
    second = cipher.encrypt_json(
        purchase_id="purchase-1",
        kind="request",
        payload=raw,
        created_at=clock.now(),
    )

    assert first.ciphertext != second.ciphertext
    assert first.encrypted_dek != second.encrypted_dek
    assert "민감한" not in first.ciphertext
    assert first.content_hash == second.content_hash
    assert cipher.decrypt_json(first) == raw


def test_encrypted_payload_is_bound_to_purchase_and_kind(clock) -> None:
    cipher = LocalEnvelopeCipher(master_key=b"k" * 32)
    encrypted = cipher.encrypt_json(
        purchase_id="purchase-1",
        kind="request",
        payload={"prompt": "secret"},
        created_at=clock.now(),
    )
    tampered = type(encrypted)(
        **{
            **{field: getattr(encrypted, field) for field in encrypted.__dataclass_fields__},
            "purchase_id": "purchase-2",
        }
    )

    try:
        cipher.decrypt_json(tampered)
    except Exception:
        pass
    else:
        raise AssertionError("AAD tampering must fail decryption")


def test_content_hash_tampering_is_detected(clock) -> None:
    cipher = LocalEnvelopeCipher(master_key=b"k" * 32)
    encrypted = cipher.encrypt_json(
        purchase_id="purchase-1",
        kind="request",
        payload={"prompt": "secret"},
        created_at=clock.now(),
    )
    tampered = type(encrypted)(
        **{
            **{field: getattr(encrypted, field) for field in encrypted.__dataclass_fields__},
            "content_hash": "sha256:" + ("0" * 64),
        }
    )

    try:
        cipher.decrypt_json(tampered)
    except EvidenceIntegrityError:
        pass
    else:
        raise AssertionError("content hash tampering must be detected")
