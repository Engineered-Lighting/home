from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.range import Range
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from . import schema
from .ids import uuid7


GOVERNED_DEPENDENCY_RELATIONS = (
    "projection_of",
    "copy_of",
    "summarizes",
    "derived_from",
    "mentions",
    "contains_copy",
    "indexed_as",
    "trained_on",
)
MAX_LINEAGE_DEPTH = 32


class LineageTraversalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DescriptorErasureResult:
    fact_version_ids: tuple[uuid.UUID, ...]
    transaction_ids: tuple[uuid.UUID, ...]
    descendant_artifact_ids: tuple[uuid.UUID, ...]


async def governed_descendants(
    connection,
    root_artifact_id: uuid.UUID,
    *,
    max_depth: int = MAX_LINEAGE_DEPTH,
) -> list[uuid.UUID]:
    if max_depth < 1 or max_depth > 128:
        raise ValueError("lineage depth must be between 1 and 128")
    rows = (
        (
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE lineage(artifact_id, depth, path, cycle) AS (
                        SELECT CAST(:root_id AS uuid), 0,
                               ARRAY[CAST(:root_id AS uuid)], false
                        UNION ALL
                        SELECT links.child_artifact_id,
                               lineage.depth + 1,
                               lineage.path || links.child_artifact_id,
                               links.child_artifact_id = ANY(lineage.path)
                          FROM lineage
                          JOIN ingest.artifact_links AS links
                            ON links.parent_artifact_id = lineage.artifact_id
                         WHERE NOT lineage.cycle
                           AND lineage.depth <= :max_depth
                           AND links.relation = ANY(CAST(:relations AS text[]))
                    )
                    SELECT artifact_id, depth, cycle
                      FROM lineage
                     WHERE depth > 0
                    """
                ),
                {
                    "root_id": str(root_artifact_id),
                    "max_depth": max_depth,
                    "relations": list(GOVERNED_DEPENDENCY_RELATIONS),
                },
            )
        )
        .mappings()
        .all()
    )
    if any(row["cycle"] for row in rows):
        raise LineageTraversalError("artifact lineage contains a cycle")
    if any(row["depth"] > max_depth for row in rows):
        raise LineageTraversalError("artifact lineage exceeds the traversal limit")
    return list(dict.fromkeys(row["artifact_id"] for row in rows))


async def apply_descriptor_erasure(
    connection,
    *,
    principal_id: uuid.UUID,
    fact_id: uuid.UUID,
    erasure_request_id: uuid.UUID,
    now: datetime,
    require_existing: bool,
) -> DescriptorErasureResult:
    # Retrieval is blocked before any content mutation. Descendant blocks are
    # added after the bounded, cycle-detecting lineage traversal succeeds.
    await connection.execute(
        insert(schema.retrieval_blocks)
        .values(
            block_id=uuid7(),
            artifact_id=fact_id,
            erasure_request_id=erasure_request_id,
        )
        .on_conflict_do_nothing(index_elements=["artifact_id"])
    )
    versions = (
        (
            await connection.execute(
                select(schema.fact_versions)
                .where(
                    schema.fact_versions.c.fact_id == fact_id,
                    schema.fact_versions.c.predicate == "place_social_descriptor",
                    schema.fact_versions.c.perspective_principal_id == principal_id,
                )
                .order_by(schema.fact_versions.c.version)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    if require_existing and not versions:
        from .errors import NotFoundError

        raise NotFoundError("owned descriptor fact does not exist")

    version_transaction_ids = [row["memory_transaction_id"] for row in versions]
    pending_transaction_ids = (
        (
            await connection.execute(
                select(schema.memory_transactions.c.transaction_id)
                .where(
                    schema.memory_transactions.c.principal_id == principal_id,
                    schema.memory_transactions.c.candidate.contains(
                        {"descriptor_fact_id": str(fact_id)}
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    transaction_ids = list(
        dict.fromkeys([*version_transaction_ids, *pending_transaction_ids])
    )
    fact_version_ids = [row["fact_version_id"] for row in versions]
    descendant_ids = await governed_descendants(connection, fact_id)
    for artifact_id in descendant_ids:
        await connection.execute(
            insert(schema.retrieval_blocks)
            .values(
                block_id=uuid7(),
                artifact_id=artifact_id,
                erasure_request_id=erasure_request_id,
            )
            .on_conflict_do_nothing(index_elements=["artifact_id"])
        )

    for version in versions:
        values: dict[str, Any] = {
            "object": {"erased": True},
            "resolution": "suppressed",
        }
        if version["system_range"].upper is None:
            values["system_range"] = Range(
                version["system_range"].lower, now, bounds="[)"
            )
        await connection.execute(
            update(schema.fact_versions)
            .where(schema.fact_versions.c.fact_version_id == version["fact_version_id"])
            .values(**values)
        )
    if transaction_ids:
        await connection.execute(
            update(schema.memory_transactions)
            .where(
                schema.memory_transactions.c.transaction_id.in_(transaction_ids),
                schema.memory_transactions.c.state.in_(
                    ["draft", "verifying", "needs_confirmation", "quarantined"]
                ),
            )
            .values(state="cancelled", updated_at=now)
        )
        await connection.execute(
            update(schema.memory_transactions)
            .where(schema.memory_transactions.c.transaction_id.in_(transaction_ids))
            .values(
                exact_text_ciphertext=None,
                exact_text_nonce=None,
                exact_text_sha256=None,
                candidate={"erased": True},
                preview={"erased": True},
                updated_at=now,
            )
        )
    if fact_version_ids:
        await connection.execute(
            update(schema.initiatives)
            .where(
                schema.initiatives.c.source_fact_version_id.in_(fact_version_ids),
                schema.initiatives.c.state.in_(["pending", "claimed", "suppressed"]),
            )
            .values(state="suppressed", suppression_reason="descriptor_erased")
        )
    artifact_ids = [fact_id, *transaction_ids, *descendant_ids]
    await connection.execute(
        update(schema.artifact_registry)
        .where(schema.artifact_registry.c.artifact_id.in_(artifact_ids))
        .values(status="erased")
    )
    return DescriptorErasureResult(
        fact_version_ids=tuple(fact_version_ids),
        transaction_ids=tuple(transaction_ids),
        descendant_artifact_ids=tuple(descendant_ids),
    )


async def invalidate_descriptor_dependents(
    connection,
    *,
    fact_id: uuid.UUID,
    source_fact_version_id: uuid.UUID,
    reason: str,
) -> None:
    await connection.execute(
        update(schema.initiatives)
        .where(
            schema.initiatives.c.source_fact_version_id == source_fact_version_id,
            schema.initiatives.c.state.in_(["pending", "claimed"]),
        )
        .values(state="suppressed", suppression_reason=reason)
    )
    descendant_ids = await governed_descendants(connection, fact_id)
    if descendant_ids:
        await connection.execute(
            update(schema.artifact_registry)
            .where(
                schema.artifact_registry.c.artifact_id.in_(descendant_ids),
                schema.artifact_registry.c.status == "active",
            )
            .values(status="stale")
        )
