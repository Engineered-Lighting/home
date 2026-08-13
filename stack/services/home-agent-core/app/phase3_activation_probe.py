"""Content-free, read-only probes for the split Phase 3 activation runner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import sys
from typing import Literal
from urllib.parse import unquote, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


CONTRACT = "phase3-activation-probe-e5ac-v1"
PASSWORD = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Probe:
    name: Literal["authority", "binding", "parents"]
    revision: str


PROBES = {
    probe.name: probe
    for probe in (
        Probe("authority", "0015_current_authority_e5a"),
        Probe("binding", "0017_authenticated_binding_e5c"),
        Probe("parents", "0021_parent_status_e5h"),
    )
}


class ActivationProbeError(RuntimeError):
    """The fixed activation state could not be proven."""


def database_url() -> str:
    raw = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise ActivationProbeError(
            "activation probe database URL is invalid"
        ) from error
    password = unquote(parsed.password or "")
    expected = (
        f"postgresql+psycopg://home_agent_owner:{password}" "@postgres:5432/home_agent"
    )
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.username != "home_agent_owner"
        or parsed.hostname != "postgres"
        or port != 5432
        or parsed.path != "/home_agent"
        or parsed.query
        or parsed.fragment
        or PASSWORD.fullmatch(password) is None
        or raw != expected
    ):
        raise ActivationProbeError("activation probe database URL is invalid")
    return raw


async def _revision(connection) -> str:
    rows = (
        (
            await connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != 1 or not isinstance(rows[0], str):
        raise ActivationProbeError("activation probe revision is invalid")
    return rows[0]


async def _authority(connection) -> dict[str, object]:
    row = (
        (
            await connection.execute(
                text(
                    """
                SELECT count(*)::integer AS promotion_count,
                       count(*) FILTER (
                         WHERE operations.evaluate_current_identity_semantic_authority(
                           promotion_id
                         ) = 'current_database_authority'
                       )::integer AS current_count
                  FROM operations.semantic_authority_promotions
                 WHERE authority_scope = 'identity_semantics'
                   AND authority_status = 'historical_authoritative_commit'
                   AND authoritative_at_commit = true
                """
                )
            )
        )
        .mappings()
        .one()
    )
    promotion_count = row["promotion_count"]
    current_count = row["current_count"]
    return {
        "semantic_authority_current": promotion_count == 1 and current_count == 1,
        "promotion_count": promotion_count,
        "current_promotion_count": current_count,
    }


async def _binding(connection) -> dict[str, object]:
    row = (
        (
            await connection.execute(
                text(
                    """
                SELECT count(*)::integer AS binding_count,
                       count(DISTINCT receipt.receipt_id)::integer AS receipt_count,
                       count(*) FILTER (
                         WHERE proposal.state = 'consumed'
                           AND request.state = 'consumed'
                           AND receipt.authority_result =
                               'current_database_authority'
                       )::integer AS exact_count
                  FROM identity.ha_user_bindings AS binding
                  JOIN operations.principal_binding_authority_receipts AS receipt
                    ON receipt.binding_id = binding.binding_id
                  JOIN identity.principal_binding_proposals AS proposal
                    ON proposal.proposal_id = binding.proposal_id
                  JOIN identity.principal_binding_requests AS request
                    ON request.request_id = proposal.request_id
                 WHERE binding.revoked_at IS NULL
                   AND binding.confirmed_by_principal_id = binding.principal_id
                   AND binding.source_artifact_id IS NOT NULL
                """
                )
            )
        )
        .mappings()
        .one()
    )
    binding_count = row["binding_count"]
    receipt_count = row["receipt_count"]
    exact_count = row["exact_count"]
    return {
        "authenticated_binding_confirmed": (
            binding_count == 1 and receipt_count == 1 and exact_count == 1
        ),
        "active_binding_count": binding_count,
        "binding_receipt_count": receipt_count,
    }


async def _parents(connection) -> dict[str, object]:
    row = (
        (
            await connection.execute(
                text(
                    """
                SELECT count(DISTINCT receipt.receipt_id)::integer
                         AS authority_receipt_count,
                       count(DISTINCT edge.receipt_edge_id)::integer
                         AS receipt_edge_count,
                       count(DISTINCT fact.fact_version_id)::integer
                         AS exact_fact_count
                  FROM operations.parent_relationship_authority_receipts AS receipt
                  JOIN operations.parent_relationship_authority_receipt_edges AS edge
                    ON edge.receipt_id = receipt.receipt_id
                  JOIN knowledge.fact_versions AS fact
                    ON fact.fact_version_id = edge.fact_version_id
                 WHERE receipt.contract_version =
                       'parent-relationship-authority-v2'
                   AND receipt.edge_count = 2
                   AND receipt.authority_result = 'committed'
                   AND fact.predicate = 'parent_of'
                   AND upper_inf(fact.system_range)
                   AND fact.authority = 'explicit_related_party'
                   AND fact.support = 'explicit_authority'
                   AND fact.contradiction = 'none'
                   AND fact.freshness = 'not_applicable'
                   AND fact.coverage = 'not_applicable'
                   AND fact.resolution = 'accepted'
                   AND fact.privacy_scope = 'private'
                """
                )
            )
        )
        .mappings()
        .one()
    )
    receipt_count = row["authority_receipt_count"]
    edge_count = row["receipt_edge_count"]
    fact_count = row["exact_fact_count"]
    return {
        "exact_parent_relationship_confirmed": (
            receipt_count == 1 and edge_count == 2 and fact_count == 2
        ),
        "parent_authority_receipt_count": receipt_count,
        "parent_receipt_edge_count": edge_count,
        "active_parent_fact_count": fact_count,
    }


async def execute(url: str, probe: Probe) -> dict[str, object]:
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=15000 "
                "-c lock_timeout=3000 "
                "-c log_parameter_max_length_on_error=0"
            )
        },
    )
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                revision = await _revision(connection)
                if revision != probe.revision:
                    raise ActivationProbeError(
                        "activation probe revision does not match"
                    )
                if probe.name == "authority":
                    evidence = await _authority(connection)
                elif probe.name == "binding":
                    evidence = await _binding(connection)
                else:
                    evidence = await _parents(connection)
    finally:
        await engine.dispose()
    return {
        "contract": CONTRACT,
        "probe": probe.name,
        "schema_revision": probe.revision,
        **evidence,
        "authoritative": False,
        "read_only": True,
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PROBES:
        print("phase3 activation probe requires one fixed operation", file=sys.stderr)
        return 64
    try:
        result = asyncio.run(execute(database_url(), PROBES[sys.argv[1]]))
    except (ActivationProbeError, OSError):
        print("phase3 activation probe failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
