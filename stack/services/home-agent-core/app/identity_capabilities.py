"""Reviewed schema compatibility for existing identity HTTP adapters.

This is capability compatibility, not deployment authorization. Startup still
requires the exact configured database revision; restore, rollout, credentials,
current-authority and database-kernel checks remain independent gates. A new
migration is denied until its function contracts and preserved or replayed role
grants have been reviewed. Never infer compatibility from revision ordering.

0047 preserves these legacy functions and grants when migrating from the
commissioned 0031 database. The hosted clone validates existing person and
relationship writes there. The legacy generic grants replay script is not a
0047 provisioning path; shared preference activation is separate.
"""
from types import MappingProxyType
from typing import Literal


IdentityCapability = Literal[
    "principal_binding_confirmation",
    "parent_relationship_confirmation",
    "owner_person_creation",
    "owner_relationship_attestation",
]

_REVIEWED_REVISIONS = MappingProxyType({
    # apply-grants.sh deliberately activates E5c only at 0017. Later function
    # existence does not imply its EXECUTE/table privileges remain available.
    "principal_binding_confirmation": frozenset({
        "0017_authenticated_binding_e5c",
    }),
    # E5e/E5f/E5h signatures are unchanged after 0021 and their stage/commit/
    # status privileges are explicitly replayed through 0031. Only revisions
    # accepted by Settings are listed; intermediate migrations are not targets.
    "parent_relationship_confirmation": frozenset({
        "0021_parent_status_e5h",
        "0027_owner_person_e5n",
        "0028_owner_partner_access_e5o",
        "0029_owner_person_role_e5p",
        "0030_relationship_vocabulary_e5q",
        "0031_relationship_uniqueness_e5r",
        "0047_personal_pref_authority_v1",
    }),
    # These current adapters require the repaired dedicated roles and the
    # seven-predicate contract. 0031 changes uniqueness indexes only. Earlier
    # revisions are intentionally not enabled by this compatibility correction.
    "owner_person_creation": frozenset({
        "0030_relationship_vocabulary_e5q",
        "0031_relationship_uniqueness_e5r",
        "0047_personal_pref_authority_v1",
    }),
    "owner_relationship_attestation": frozenset({
        "0030_relationship_vocabulary_e5q",
        "0031_relationship_uniqueness_e5r",
        "0047_personal_pref_authority_v1",
    }),
})


def supports_identity_capability(revision: str, capability: IdentityCapability) -> bool:
    """Unknown revisions or capabilities never acquire authority implicitly."""
    if not isinstance(revision, str) or not isinstance(capability, str):
        return False
    return revision in _REVIEWED_REVISIONS.get(capability, frozenset())
