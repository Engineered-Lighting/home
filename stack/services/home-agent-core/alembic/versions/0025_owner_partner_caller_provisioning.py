"""Provision the caller for the owner-attested partner kernel.

Revision ID: 0025_owner_partner_caller_e5l
Revises: 0024_owner_partner_e5k
Create Date: 2026-08-28

0024 left identity.commit_owner_partner_relationship_e5k unreachable on purpose:
it had no GRANT, exactly as E5d left E5f dormant until E5f's own reviewed
change. This is that change for E5k.

It grants EXECUTE to one role and nothing else. It does not widen
home_agent_api, which holds no identity write privilege at all and must keep
holding none -- the runtime API reaches a kernel only through the separate
binding-committer credential, so a compromised API role still cannot write a
fact. That separation is the reason a kernel exists rather than an INSERT.

The kernel's own guards remain the real gate: the caller must be the committer
by session_user, must be running as the kernel by current_user, and must NOT be
able to SET ROLE into it. Granting EXECUTE does not weaken any of those; it
only makes the guarded path callable.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision = "0025_owner_partner_caller_e5l"
down_revision = "0024_owner_partner_e5k"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_partner_relationship_kernel"
SIGNATURE = (
    "uuid, text, uuid, text, uuid, uuid, uuid, uuid, uuid, uuid, uuid, "
    "uuid, uuid, uuid, uuid"
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $provision$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_authid WHERE rolname = '{CALLER_ROLE}'
          ) THEN
            RAISE EXCEPTION
              'owner_partner_e5l_caller_absent'
              USING ERRCODE = '42704';
          END IF;

          -- The kernel refuses a caller who can SET ROLE into it, so a
          -- membership grant here would make the kernel permanently
          -- unreachable rather than more permissive. Fail loudly instead of
          -- provisioning something that can never work.
          IF pg_catalog.pg_has_role('{CALLER_ROLE}', '{KERNEL_ROLE}', 'USAGE')
             THEN
            RAISE EXCEPTION
              'owner_partner_e5l_caller_is_kernel_member'
              USING ERRCODE = '42501';
          END IF;
        END
        $provision$;
        """
    )

    # Revoke first: PUBLIC holds EXECUTE on new functions by default, which
    # would make every role a caller.
    op.execute(
        f"REVOKE ALL ON FUNCTION "
        f"identity.commit_owner_partner_relationship_e5k({SIGNATURE}) "
        f"FROM PUBLIC;"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"identity.commit_owner_partner_relationship_e5k({SIGNATURE}) "
        f"TO {CALLER_ROLE};"
    )


def downgrade() -> None:
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION "
        f"identity.commit_owner_partner_relationship_e5k({SIGNATURE}) "
        f"FROM {CALLER_ROLE};"
    )
