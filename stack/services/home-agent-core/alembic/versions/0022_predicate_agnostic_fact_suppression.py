"""Suppress erased people on the object side of every predicate, not just parent_of.

Revision ID: 0022_fact_suppression_e5i
Revises: 0021_parent_status_e5h
Create Date: 2026-08-28

``privacy.identity_fact_is_blocked`` is the USING and WITH CHECK expression of
the restrictive policy ``knowledge_fact_versions_e2_identity_suppression``. It
returned false for any predicate other than ``parent_of`` before it ever looked
at ``target_object ->> 'person_id'``, so the object side of a fact was checked
for exactly one predicate.

That was sound while ``parent_of`` was the only person-to-person predicate. It
stops being sound the moment a second one exists: an erased person would remain
visible as the object of a relationship, which is precisely the disclosure the
erasure kernel exists to prevent. The hole has to close before the predicate
lands, not after, so this revision deliberately precedes any new predicate.

The replacement is strictly stronger. It returns true in every case the previous
body did, plus the cases the predicate guard was skipping; it can never return
false where the old body returned true. No fact currently suppressed becomes
visible, and the subject-side and perspective-side arms are untouched.

The signature is unchanged, so the policies that reference it need no edit.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0022_fact_suppression_e5i"
down_revision: str = "0021_parent_status_e5h"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_UUID_PATTERN = (
    "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'\n"
    "                 '[0-9a-f]{4}-[0-9a-f]{12}$'"
)


def upgrade() -> None:
    # CREATE OR REPLACE keeps the existing policies bound to this function.
    # Unconditional: unlike 0010, there is no shape to branch on, so this runs
    # identically on a bootstrap database and on a migrated deployment.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION privacy.identity_fact_is_blocked(
          target_subject_type text,
          target_subject_id uuid,
          target_predicate text,
          target_object jsonb,
          target_perspective_principal_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          object_person_text text;
        BEGIN
          IF privacy.identity_principal_is_blocked(
               target_perspective_principal_id
             ) THEN
            RETURN true;
          END IF;
          IF target_subject_type = 'person'
             AND privacy.identity_person_is_blocked(target_subject_id) THEN
            RETURN true;
          END IF;
          -- Predicate-agnostic: any fact whose object names a person is
          -- suppressed when that person is erased. A predicate whose object is
          -- not a person simply has no person_id and falls through to false.
          IF pg_catalog.jsonb_typeof(target_object) <> 'object' THEN
            RETURN false;
          END IF;
          object_person_text := target_object ->> 'person_id';
          IF object_person_text IS NULL
             OR object_person_text !~*
               {_UUID_PATTERN} THEN
            RETURN false;
          END IF;
          RETURN privacy.identity_person_is_blocked(object_person_text::uuid);
        END
        $function$;
        """
    )


def downgrade() -> None:
    # Restores the parent_of-only guard exactly as 0012 defined it. This
    # reopens the object-side hole for every other predicate, so it is only
    # safe on a database that holds no non-parent_of person-object facts.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION privacy.identity_fact_is_blocked(
          target_subject_type text,
          target_subject_id uuid,
          target_predicate text,
          target_object jsonb,
          target_perspective_principal_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          object_person_text text;
        BEGIN
          IF privacy.identity_principal_is_blocked(
               target_perspective_principal_id
             ) THEN
            RETURN true;
          END IF;
          IF target_subject_type = 'person'
             AND privacy.identity_person_is_blocked(target_subject_id) THEN
            RETURN true;
          END IF;
          IF target_predicate <> 'parent_of'
             OR pg_catalog.jsonb_typeof(target_object) <> 'object' THEN
            RETURN false;
          END IF;
          object_person_text := target_object ->> 'person_id';
          IF object_person_text IS NULL
             OR object_person_text !~*
               {_UUID_PATTERN} THEN
            RETURN false;
          END IF;
          RETURN privacy.identity_person_is_blocked(object_person_text::uuid);
        END
        $function$;
        """
    )
