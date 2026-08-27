"""DIAGNOSTIC ONLY -- do not merge.

apply-grants.sh pins one aggregate digest over the E3 catalog manifest
(apply-grants.sh:3771-4129) and reports only that aggregate when it mismatches:

    identity finalizer E3 catalog manifest mismatch
    expected=123326a4... actual=47e63bd8...

The aggregate cannot say WHICH relation drifted. This computes the same inputs
per relation -- owner, relkind, persistence, RLS flags, replica identity,
comment, columns, constraints, indexes, policies, triggers; deliberately not
ACLs, which the manifest does not hash -- and compares them against the values
observed on the live deployment at revision 0017_authenticated_binding_e5c.

A failure here is the point: the assertion diff names the relations whose
catalog differs between the hosted gate and the deployment, and the reported
alembic revision says which state produced them.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


# Whichever phase runs first supplies one of these; the revision is reported
# alongside the result so the numbers can be read in context.
OWNER_DATABASE_ENVS = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_OWNER_DATABASE_URL",
    "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_OWNER_DATABASE_URL",
    "TEST_PHASE3_IDENTITY_SEMANTIC_CUTOVER_E4_OWNER_DATABASE_URL",
    "TEST_PHASE3_IDENTITY_FINALIZER_E3_OWNER_DATABASE_URL",
)

# Observed on the deployment at 0017_authenticated_binding_e5c.
DEPLOYMENT_FINGERPRINTS = {
    "operations.legacy_identity_writer_evidence": "a62740790b9fc3e6cf0e807c7d4d87702ae5014e7dd2a474c2da9fb7abf625f5",
    "operations.privacy_cutover_check_receipts": "b3d99d744b35846e456d2817f9f00951900bb6ce3968b7a9bab1c8edb0170271",
    "operations.reviewed_identity_finalizer_admissions": "a9a3a1aafd54a7855d09fe96604f94404fbb3e6a98b351434772e0ccef25e123",
    "operations.reviewed_identity_migration_decisions": "d0cbc875206fe13c61901129db0bd5b0f0888b9c3da14dbe71f7015e1b5d3cd1",
    "operations.reviewed_identity_migration_erasure_impacts": "9260325bd8d033ce00d1b26f0e26b5dd42e4cb3f7a6ac644dacc138dff85cddc",
    "operations.reviewed_identity_migration_finalizations": "6b0e70c08e0378062b52eda4548e07979671222ad3b52b8926b6f596ef0cf19b",
    "operations.reviewed_identity_migration_item_receipts": "f650181987c4a7c9b7b2570156b5324913cb09598e08815bf9deaf353010fe2e",
    "operations.reviewed_identity_migration_projection_lineage": "4879a69eb8754056d86638300f9706c7b36e5d2a72b6b65b345fded4ff256292",
    "operations.reviewed_identity_migration_projection_subjects": "76bac3229074dbc65db7678c6cb0ff2c6eb02a250315fb67c914f417207c40db",
    "operations.reviewed_identity_migration_runs": "3d75671ec908381347216dd6fd561a0fc5b7f0a79aa0d6bfafbf711d4ee9cf9e",
    "operations.reviewed_identity_migration_source_items": "38aa26866028c6305d9a0401b59c2300c1e06282fa8a13cc0f4a01c0e1d8c9b0",
    "operations.semantic_authority_cutovers": "80630333e2b89aacdf67010a372878bddb197b0087ee90da590bd6f14f6a6d1c",
    "privacy.identity_semantic_write_fence": "88243895033a209d54d4c04f14682fa589d731957077a676a15f689d1697c8a4",
}

FINGERPRINT_SQL = """
WITH targets(name) AS (
  VALUES
    ('operations.reviewed_identity_migration_runs'),
    ('operations.reviewed_identity_migration_source_items'),
    ('operations.reviewed_identity_migration_decisions'),
    ('operations.reviewed_identity_migration_item_receipts'),
    ('operations.reviewed_identity_migration_finalizations'),
    ('operations.reviewed_identity_migration_projection_lineage'),
    ('operations.reviewed_identity_migration_projection_subjects'),
    ('operations.reviewed_identity_migration_erasure_impacts'),
    ('operations.legacy_identity_writer_evidence'),
    ('operations.privacy_cutover_check_receipts'),
    ('operations.semantic_authority_cutovers'),
    ('operations.reviewed_identity_finalizer_admissions'),
    ('privacy.identity_semantic_write_fence')
),
rel AS (
  SELECT t.name, c.oid, c.relowner, c.relkind, c.relpersistence,
         c.relrowsecurity, c.relforcerowsecurity, c.relreplident
    FROM targets t JOIN pg_catalog.pg_class c ON c.oid = pg_catalog.to_regclass(t.name)
),
cols AS (
  SELECT rel.oid, jsonb_agg(jsonb_build_array(
           a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
           a.attidentity, a.attgenerated,
           CASE WHEN a.attcollation = 0 THEN NULL ELSE a.attcollation::regcollation::text END,
           pg_get_expr(d.adbin, d.adrelid, true)) ORDER BY a.attnum) AS v
    FROM rel JOIN pg_catalog.pg_attribute a
      ON a.attrelid = rel.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
   GROUP BY rel.oid
),
cons AS (
  SELECT rel.oid, COALESCE(jsonb_agg(jsonb_build_array(
           k.conname, k.contype, k.condeferrable, k.condeferred, k.convalidated,
           k.conkey::text,
           CASE WHEN k.confrelid = 0 THEN NULL ELSE k.confrelid::regclass::text END,
           k.confkey::text, k.confupdtype, k.confdeltype, k.confmatchtype,
           pg_get_constraintdef(k.oid)) ORDER BY k.conname)
         FILTER (WHERE k.oid IS NOT NULL), '[]'::jsonb) AS v
    FROM rel LEFT JOIN pg_catalog.pg_constraint k ON k.conrelid = rel.oid
   GROUP BY rel.oid
),
idx AS (
  SELECT rel.oid, COALESCE(jsonb_agg(jsonb_build_array(
           i.indexrelid::regclass::text, pg_get_indexdef(i.indexrelid),
           i.indisunique, i.indisprimary, i.indisvalid)
           ORDER BY i.indexrelid::regclass::text)
         FILTER (WHERE i.indexrelid IS NOT NULL), '[]'::jsonb) AS v
    FROM rel LEFT JOIN pg_catalog.pg_index i ON i.indrelid = rel.oid
   GROUP BY rel.oid
),
pol AS (
  SELECT rel.oid, COALESCE(jsonb_agg(jsonb_build_array(
           p.polname, p.polcmd::text, p.polpermissive,
           COALESCE((SELECT string_agg(ro.rolname, ',' ORDER BY ro.rolname)
                       FROM pg_catalog.pg_authid ro WHERE ro.oid = ANY (p.polroles)), 'PUBLIC'),
           COALESCE(pg_get_expr(p.polqual, p.polrelid, true), ''),
           COALESCE(pg_get_expr(p.polwithcheck, p.polrelid, true), ''))
           ORDER BY p.polname)
         FILTER (WHERE p.oid IS NOT NULL), '[]'::jsonb) AS v
    FROM rel LEFT JOIN pg_catalog.pg_policy p ON p.polrelid = rel.oid
   GROUP BY rel.oid
),
trg AS (
  SELECT rel.oid, COALESCE(jsonb_agg(jsonb_build_array(
           tg.tgname, tg.tgenabled, tg.tgtype, pg_get_triggerdef(tg.oid, true))
           ORDER BY tg.tgname)
         FILTER (WHERE tg.oid IS NOT NULL), '[]'::jsonb) AS v
    FROM rel LEFT JOIN pg_catalog.pg_trigger tg
      ON tg.tgrelid = rel.oid AND NOT tg.tgisinternal
   GROUP BY rel.oid
)
SELECT rel.name, encode(sha256(convert_to(jsonb_build_object(
         'owner', pg_get_userbyid(rel.relowner),
         'kind', rel.relkind, 'persistence', rel.relpersistence,
         'rls', rel.relrowsecurity, 'force_rls', rel.relforcerowsecurity,
         'replica_identity', rel.relreplident,
         'comment', obj_description(rel.oid, 'pg_class'),
         'columns', cols.v, 'constraints', cons.v, 'indexes', idx.v,
         'policies', pol.v, 'triggers', trg.v
       )::text, 'UTF8')), 'hex')
  FROM rel
  JOIN cols ON cols.oid = rel.oid
  JOIN cons ON cons.oid = rel.oid
  JOIN idx  ON idx.oid  = rel.oid
  JOIN pol  ON pol.oid  = rel.oid
  JOIN trg  ON trg.oid  = rel.oid
 ORDER BY rel.name
"""


def _configured_url() -> str | None:
    for name in OWNER_DATABASE_ENVS:
        value = os.getenv(name)
        if value:
            return value
    return None


async def _collect(url: str) -> tuple[str, dict[str, str]]:
    engine = create_async_engine(
        make_url(url).set(drivername="postgresql+psycopg"),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            rows = (await connection.execute(text(FINGERPRINT_SQL))).all()
        return str(revision), {name: digest for name, digest in rows}
    finally:
        await engine.dispose()


@pytest.mark.skipif(_configured_url() is None, reason="no owner database configured")
def test_report_e3_catalog_manifest_fingerprints() -> None:
    revision, observed = asyncio.run(_collect(_configured_url()))

    report = "\n".join(
        f"  E3_FINGERPRINT revision={revision} {name} {digest}"
        for name, digest in sorted(observed.items())
    )
    print(f"\nE3 catalog manifest fingerprints at revision {revision}:\n{report}")

    differing = sorted(
        name
        for name in set(observed) | set(DEPLOYMENT_FINGERPRINTS)
        if observed.get(name) != DEPLOYMENT_FINGERPRINTS.get(name)
    )
    assert not differing, (
        f"gate revision {revision}; relations differing from the deployment: "
        f"{differing}\n{report}"
    )
