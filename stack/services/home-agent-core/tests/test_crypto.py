from __future__ import annotations

import pytest

from app.crypto import FieldCipher, SealedValue


def test_cipher_is_bound_to_purpose_and_artifact() -> None:
    cipher = FieldCipher(bytes(range(32)))
    sealed = cipher.seal(
        b"private-coordinate", purpose="visit-anchor", artifact_id="visit-1"
    )

    assert (
        cipher.open(sealed, purpose="visit-anchor", artifact_id="visit-1")
        == b"private-coordinate"
    )
    with pytest.raises(Exception):
        cipher.open(sealed, purpose="place-locator", artifact_id="visit-1")
    with pytest.raises(Exception):
        cipher.open(sealed, purpose="visit-anchor", artifact_id="visit-2")


def test_digest_check_rejects_swapped_digest() -> None:
    cipher = FieldCipher(b"k" * 32)
    sealed = cipher.seal(b"value", purpose="test", artifact_id="one")
    corrupted = SealedValue(sealed.nonce, sealed.ciphertext, "0" * 64)

    with pytest.raises(ValueError, match="digest"):
        cipher.open(corrupted, purpose="test", artifact_id="one")
