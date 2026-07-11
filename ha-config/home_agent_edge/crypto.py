"""Authenticated encryption for the edge spool."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptedEnvelopeCodec:
    """Encode edge envelopes with AES-256-GCM.

    The key is stored separately from SQLite.  There is deliberately no
    plaintext fallback: losing/unloading the key turns into an explicit edge
    failure rather than silently writing private location data unencrypted.
    """

    AAD = b"home-agent-edge-spool:v1"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Home Agent Edge spool key must be exactly 32 bytes")
        self._cipher = AESGCM(key)
        self._subject_tag_key = hmac.new(
            key,
            b"home-agent-edge:v1:spool-subject-tag",
            hashlib.sha256,
        ).digest()

    @classmethod
    def from_key_file(cls, key_path: str | Path, *, create: bool = True) -> "EncryptedEnvelopeCodec":
        path = Path(key_path)
        if not path.exists():
            if not create:
                raise FileNotFoundError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            key = os.urandom(32)
            encoded = base64.urlsafe_b64encode(key)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)
        else:
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError(
                    "Home Agent Edge spool key file must be mode 0600 or stricter"
                )
            encoded = path.read_bytes().strip()
            key = base64.urlsafe_b64decode(encoded)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return cls(key)

    def encode(self, envelope: Mapping[str, Any]) -> bytes:
        plaintext = json.dumps(
            dict(envelope), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, plaintext, self.AAD)

    def decode(self, payload: bytes) -> dict[str, Any]:
        if len(payload) < 13:
            raise ValueError("Encrypted envelope is truncated")
        nonce, ciphertext = payload[:12], payload[12:]
        decoded = json.loads(self._cipher.decrypt(nonce, ciphertext, self.AAD))
        if not isinstance(decoded, dict):
            raise ValueError("Encrypted envelope must decode to an object")
        return decoded

    def subject_tag(self, subject_kind: str, value: str) -> str:
        """Return a purpose-bound, non-reversible spool routing tag."""

        if subject_kind not in {"ha_user"} or not value:
            raise ValueError("invalid spool subject tag input")
        material = f"{subject_kind}:{value.strip().lower()}".encode("utf-8")
        return hmac.new(self._subject_tag_key, material, hashlib.sha256).hexdigest()
