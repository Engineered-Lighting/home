"""Make the owner-attested partner kernel executable at all.

Revision ID: 0028_owner_partner_access_e5o
Revises: 0027_owner_person_e5n
Create Date: 2026-08-28

``identity.commit_owner_partner_relationship_e5k`` has never run. It cannot:
five independent faults each abort it, and the first aborts it on its own
opening guard. Every one was found by executing the function against a real
database; none is visible in any single diff, and the static suite that shipped
alongside it was entirely green.

1. **The function is owned by the wrong role.** 0026 restated the signature and
   used ``CREATE OR REPLACE FUNCTION``, which for a *changed* signature is a
   plain ``CREATE``. It never issued ``ALTER FUNCTION ... OWNER TO`` the way
   0024 does, so the seventeen-argument function is owned by whoever ran
   alembic. ``SECURITY DEFINER`` sets ``current_user`` to the owner, and the
   function's own first guard demands the kernel role, so it raises
   ``owner_partner_e5k_role_invalid`` before reaching the write fence.

2. **Both overloads are live.** 0024 created fifteen arguments; 0026 created
   seventeen, the last two with defaults. The adapter passes fifteen, which
   matches both, so the call fails to resolve at all: SQLSTATE 42725,
   ``function ... is not unique``. Dropping 0024's overload leaves the adapter
   binding to 0026's defaults, which is what it was always meant to reach.

3. **The kernel role held no privilege anywhere** -- not even ``USAGE`` on the
   schemas. It is a *different role* from E5f's
   ``home_agent_parent_relationship_kernel`` -- partner, not parent -- and that
   one-word difference is why this went unnoticed: every privilege check
   written against the parent role passes.

4. **RLS shuts it out of its own receipt ledger.** 0024 forced row-level
   security on both receipt tables with a single
   ``USING (session_user = 'home_agent_owner')`` policy -- but SECURITY DEFINER
   changes ``current_user``, not ``session_user``. Inside the kernel
   ``session_user`` is the committer, so the replay SELECT returns nothing and
   the receipt INSERT is rejected. The table denied its own writer.

5. The same shape on the person kernel: 0027 granted
   ``home_agent_identity_finalizer_kernel`` its columns on
   ``privacy.artifact_registry`` but added no policy, and that table forces RLS.
   A grant and a row policy are different gates.

**Faults 3's remedy is deliberately not here.** Kernel grants live in
``apply-grants.sh``, where E5f's and E5n's do, for a reason this revision cannot
work around: the erasure quarantine block near the top of that script revokes
ALL PRIVILEGES on ``privacy.identity_person_is_blocked(uuid)`` from every role
in ``pg_roles``, and the script runs after alembic. An EXECUTE grant issued by a
migration is therefore removed on the next deploy. The grants for this role were
added to ``apply-grants.sh`` alongside the parent kernel's.

What remains here is what genuinely belongs in a migration: the function's
owner, the removal of the superseded overload, and the row policies -- which is
where E5f's policies live too.

The policies mirror E5f exactly, including the third clause: a caller able to
SET ROLE into the kernel is refused at the row level as well as inside the
function, so the row policy never becomes the weaker of the two checks.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0028_owner_partner_access_e5o"
down_revision: str | None = "0027_owner_person_e5n"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

KERNEL_ROLE = "home_agent_partner_relationship_kernel"
PERSON_KERNEL_ROLE = "home_agent_identity_finalizer_kernel"
CALLER_ROLE = "home_agent_binding_committer"

# 0024's overload, superseded by 0026's and dropped here so the adapter's
# fifteen-argument call resolves to exactly one function.
SUPERSEDED_SIGNATURE = (
    "uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "uuid, uuid, uuid, uuid"
)
# 0026's, whose last two arguments carry defaults.
CURRENT_SIGNATURE = (
    "uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "uuid, uuid, uuid, uuid, uuid, text"
)

# The same triple the kernel enforces internally: right caller, running as the
# kernel, and unable to become the kernel.
KERNEL_PREDICATE = (
    f"session_user = '{CALLER_ROLE}' "
    f"AND current_user = '{KERNEL_ROLE}' "
    f"AND NOT pg_catalog.pg_has_role(session_user, '{KERNEL_ROLE}', 'SET')"
)
PERSON_KERNEL_PREDICATE = (
    f"session_user = '{CALLER_ROLE}' "
    f"AND current_user = '{PERSON_KERNEL_ROLE}' "
    f"AND NOT pg_catalog.pg_has_role(session_user, '{PERSON_KERNEL_ROLE}', 'SET')"
)

# Tables the kernel only reads, and only where a policy is actually required.
# identity.people already carries identity_people_e2_acl_preservation, an ALL
# policy to PUBLIC with USING (true), so a second permissive policy would admit
# nothing new. identity.privacy_directives does not enable RLS at all, so a
# policy on it is inert. Both were verified against the deployed database.
#
# Adding a policy is not free: the E5b catalog contract digests the policies on
# these very tables, so every unnecessary one is a deploy that fails for no
# gain. ha_user_bindings genuinely needs one -- it forces RLS and no existing
# policy admits this role.
READ_TABLES = (("identity.ha_user_bindings",),)

# Tables the kernel reads and writes.
WRITE_TABLES = (
    ("knowledge.fact_versions",),
    ("knowledge.fact_support",),
    ("knowledge.memory_transactions",),
    ("privacy.artifact_registry",),
    ("operations.partner_relationship_authority_receipts",),
    ("operations.partner_relationship_authority_receipt_edges",),
)


def _stem(qualified: str) -> str:
    return qualified.split(".", 1)[1]


def upgrade() -> None:
    # Fault 1: give the function the owner its own guard requires.
    op.execute(
        f"ALTER FUNCTION identity.commit_owner_partner_relationship_e5k("
        f"{CURRENT_SIGNATURE}) OWNER TO {KERNEL_ROLE};"
    )

    # Fault 2: remove the ambiguity so a fifteen-argument call resolves.
    op.execute(
        f"DROP FUNCTION IF EXISTS "
        f"identity.commit_owner_partner_relationship_e5k("
        f"{SUPERSEDED_SIGNATURE});"
    )

    # Fault 4: the row gate, on every table the kernel touches.
    for (table,) in READ_TABLES:
        op.execute(
            f"CREATE POLICY {_stem(table)}_e5o_kernel_select ON {table} "
            f"FOR SELECT TO {KERNEL_ROLE} USING ({KERNEL_PREDICATE});"
        )

    for (table,) in WRITE_TABLES:
        op.execute(
            f"CREATE POLICY {_stem(table)}_e5o_kernel_select ON {table} "
            f"FOR SELECT TO {KERNEL_ROLE} USING ({KERNEL_PREDICATE});"
        )
        op.execute(
            f"CREATE POLICY {_stem(table)}_e5o_kernel_insert ON {table} "
            f"FOR INSERT TO {KERNEL_ROLE} WITH CHECK ({KERNEL_PREDICATE});"
        )

    # Fault 5: 0027 granted the person kernel its columns and left the row gate
    # shut.
    op.execute(
        f"CREATE POLICY owner_person_e5o_kernel_select "
        f"ON privacy.artifact_registry "
        f"FOR SELECT TO {PERSON_KERNEL_ROLE} "
        f"USING ({PERSON_KERNEL_PREDICATE});"
    )
    op.execute(
        f"CREATE POLICY owner_person_e5o_kernel_insert "
        f"ON privacy.artifact_registry "
        f"FOR INSERT TO {PERSON_KERNEL_ROLE} "
        f"WITH CHECK ({PERSON_KERNEL_PREDICATE});"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS owner_person_e5o_kernel_insert "
        "ON privacy.artifact_registry;"
    )
    op.execute(
        "DROP POLICY IF EXISTS owner_person_e5o_kernel_select "
        "ON privacy.artifact_registry;"
    )

    for (table,) in WRITE_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {_stem(table)}_e5o_kernel_insert ON {table};"
        )
        op.execute(
            f"DROP POLICY IF EXISTS {_stem(table)}_e5o_kernel_select ON {table};"
        )

    for (table,) in READ_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {_stem(table)}_e5o_kernel_select ON {table};"
        )

    # 0024's superseded overload is not recreated: it was unreachable while it
    # existed, and restoring it would restore the ambiguity. Ownership returns
    # to the migration runner, which is the state 0026 left behind.
    op.execute(
        f"ALTER FUNCTION identity.commit_owner_partner_relationship_e5k("
        f"{CURRENT_SIGNATURE}) OWNER TO CURRENT_USER;"
    )
