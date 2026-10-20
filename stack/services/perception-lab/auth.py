"""Provisioned site-scoped Lab credentials, separate from household identity."""
from dataclasses import dataclass
import hashlib
import hmac


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    site_id: str
    capabilities: frozenset

    def require(self, capability):
        if capability not in self.capabilities:
            raise AuthorizationError("credential lacks required capability")


class Authorizer:
    def __init__(self, site_id, grants):
        self.site_id = site_id
        self.grants = tuple(grants)
        for grant in self.grants:
            digest = grant.get("token_sha256", "")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("credentials require provisioned SHA-256 digests")
            if grant.get("site_id") != site_id or not grant.get("subject"):
                raise ValueError("credential site/subject mismatch")

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthorizationError("Lab bearer credential required")
        token = authorization[7:]
        if not 32 <= len(token) <= 512:
            raise AuthorizationError("invalid credential")
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for grant in self.grants:
            if hmac.compare_digest(digest, grant["token_sha256"]):
                match = grant
        if match is None:
            raise AuthorizationError("invalid credential")
        return Principal(match["subject"], self.site_id, frozenset(match.get("capabilities", ())))
