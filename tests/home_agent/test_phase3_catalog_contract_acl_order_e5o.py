"""The 0011 system-catalog contract must compare privileges, not grant order.

PostgreSQL ACL arrays preserve the order privileges were granted in. Two
databases holding identical privileges therefore hash differently if the grants
were applied in a different order — which is exactly what happened between a
freshly provisioned CI database and a deployment whose grants accumulated over
time. No single pinned digest could satisfy both, so the contract now sorts ACL
entries before hashing.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION = (
    ROOT
    / "stack/services/home-agent-core/alembic/versions"
    / "0011_identity_erasure_schema_foundation.py"
)


def _read() -> str:
    return REVISION.read_text(encoding="utf-8")


def test_e5o_contract_sorts_acl_entries_before_hashing() -> None:
    source = _read()

    assert "canonical_acl AS (" in source
    # entries are split, sorted, and reassembled
    assert "pg_catalog.string_agg(" in source
    assert "acl_entry, ',' ORDER BY acl_entry" in source
    assert "pg_catalog.string_to_array(" in source


def test_e5o_digest_is_computed_over_the_canonicalised_rows() -> None:
    """The hash must read the sorted CTE, not the raw one."""

    source = _read()

    assert ") AS contract_digest\n              FROM canonical_acl" in source
    # the raw relation is still the input to canonical_acl, but must not be the
    # direct input to the digest
    assert ") AS contract_digest\n              FROM system_contract" not in source


def test_e5o_null_acls_stay_distinguishable_from_empty_ones() -> None:
    """A default (NULL) ACL must not be normalised into an empty ACL."""

    source = _read()

    assert "WHEN raw_acl = '<NULL>' THEN raw_acl" in source


def test_e5o_pinned_digest_matches_the_canonicalised_contract() -> None:
    source = _read()

    assert "PINNED_SYSTEM_CATALOG_CONTRACT_ROWS = 6563" in source
    assert (
        '"deccb4dd1732566742b90b0ef2f840a5ac35025267a87cbff50719917297d908"' in source
    )
    # the pre-canonicalisation digest must be gone
    assert "5f9ee4e902a42d5880545f7d619a8fb95b10b92b203589cd530c60e835fc12a3" not in source
