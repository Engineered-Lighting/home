"""Bounded request identity memory for one producer/control scope.

Recent receipts retain their original outcome. The fixed tombstone filter has
false positives and no false negatives for IDs observed by this instance.
An evicted or colliding identity therefore requires a new request identity.
"""
import hashlib
import re
from collections import OrderedDict

MAX_RECENT = 128
TOMBSTONE_BITS = 1 << 18
HASH_COUNT = 4
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


def _valid(request_id, fingerprint):
    return (type(request_id) is str and IDENTIFIER.fullmatch(request_id) is not None
            and type(fingerprint) is str and FINGERPRINT.fullmatch(fingerprint) is not None)


class ReceiptLedger:
    """No payloads, clocks, expiry extension, or IO. Replace the instance on scope changes."""

    def __init__(self):
        self._recent = OrderedDict()
        self._tombstones = bytearray(TOMBSTONE_BITS // 8)

    @staticmethod
    def _indices(request_id):
        digest = hashlib.sha256(request_id.encode("ascii")).digest()
        return tuple(int.from_bytes(digest[index * 8:(index + 1) * 8], "little") % TOMBSTONE_BITS
                     for index in range(HASH_COUNT))

    def check(self, request_id, fingerprint):
        """Return exact identity knowledge, the original outcome, and a fixed reason.

        Only (False, False, None) is a fresh identity. A tombstone-only result
        has known=False because its old fingerprint is unavailable, and must
        decline with request_identity_unavailable.
        """
        if not _valid(request_id, fingerprint):
            return False, False, "invalid_request_identity"
        recent = self._recent.get(request_id)
        if recent is not None:
            if recent[0] != fingerprint:
                return True, False, "request_id_conflict"
            return True, recent[1], "idempotent_retry"
        if all(self._tombstones[index // 8] & (1 << (index % 8)) for index in self._indices(request_id)):
            return False, False, "request_identity_unavailable"
        return False, False, None

    def record(self, request_id, fingerprint, accepted):
        """Keep the first outcome. Conflicts and uncertain identities cannot be promoted."""
        if type(accepted) is not bool:
            raise ValueError("invalid_receipt_outcome")
        known, _outcome, reason = self.check(request_id, fingerprint)
        if known and reason == "idempotent_retry":
            return
        if reason is not None:
            raise ValueError(reason)
        for index in self._indices(request_id):
            self._tombstones[index // 8] |= 1 << (index % 8)
        if len(self._recent) == MAX_RECENT:
            self._recent.popitem(last=False)
        self._recent[request_id] = fingerprint, accepted
