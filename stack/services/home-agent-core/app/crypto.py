from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class SealedValue:
    nonce: bytes
    ciphertext: bytes
    sha256: str


class FieldCipher:
    """Purpose-bound AES-256-GCM envelope for sensitive local values."""

    def __init__(self, key: bytes, *, key_id: str = "runtime-v1") -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be exactly 32 bytes")
        self._aead = AESGCM(key)
        self._digest_key = hmac.new(
            key, b"home-agent:v1:ciphertext-integrity-digest", hashlib.sha256
        ).digest()
        self.key_id = key_id

    def seal(self, value: bytes, *, purpose: str, artifact_id: str) -> SealedValue:
        nonce = os.urandom(12)
        aad = self._aad(purpose, artifact_id)
        ciphertext = self._aead.encrypt(nonce, value, aad)
        return SealedValue(
            nonce=nonce,
            ciphertext=ciphertext,
            sha256=hmac.new(self._digest_key, value, hashlib.sha256).hexdigest(),
        )

    def open(self, sealed: SealedValue, *, purpose: str, artifact_id: str) -> bytes:
        value = self._aead.decrypt(
            sealed.nonce,
            sealed.ciphertext,
            self._aad(purpose, artifact_id),
        )
        expected = hmac.new(self._digest_key, value, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sealed.sha256):
            raise ValueError("decrypted value digest mismatch")
        return value

    @staticmethod
    def _aad(purpose: str, artifact_id: str) -> bytes:
        if not purpose or not artifact_id:
            raise ValueError("purpose and artifact_id are required")
        return f"home-agent:v1:{purpose}:{artifact_id}".encode()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
