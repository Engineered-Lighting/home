from __future__ import annotations

from app import schema
from app.api import semantic_router
from sqlalchemy import CheckConstraint


def test_greenfield_authority_has_expected_schema_boundaries() -> None:
    schemas = {table.schema for table in schema.metadata.tables.values()}
    assert {
        "ingest",
        "identity",
        "knowledge",
        "engagement",
        "privacy",
        "operations",
    } <= schemas


def test_no_raw_payload_or_plaintext_locator_in_postgres_schema() -> None:
    envelope_columns = set(schema.envelopes.c.keys())
    locator_columns = set(schema.place_locators.c.keys())
    transaction_columns = set(schema.memory_transactions.c.keys())

    assert "payload" not in envelope_columns
    assert "latitude" not in locator_columns
    assert "longitude" not in locator_columns
    assert "exact_text" not in transaction_columns
    assert {"ciphertext", "nonce", "locator_sha256"} <= locator_columns
    assert {
        "exact_text_ciphertext",
        "exact_text_nonce",
        "exact_text_sha256",
    } <= transaction_columns


def test_rollout_authorization_is_content_free_and_transition_unique() -> None:
    columns = set(schema.rollout_authorizations.c.keys())
    assert columns == {
        "authorization_id",
        "operator_request_id",
        "from_mode",
        "to_mode",
        "rule_version",
        "policy_version",
        "policy_digest",
        "input_digest",
        "worker_instance_id",
        "worker_success_sequence",
        "worker_kernel_version",
        "worker_maintenance_succeeded_at",
        "worker_proof_digest",
        "readiness_evaluated_at",
        "authorized_at",
    }
    assert not columns & {
        "person_id",
        "principal_id",
        "entity_id",
        "latitude",
        "longitude",
        "payload",
        "name",
    }
    unique_names = {
        constraint.name
        for constraint in schema.rollout_authorizations.constraints
        if getattr(constraint, "name", None)
    }
    assert any(name.endswith("rollout_transition_once") for name in unique_names)
    transition = next(
        constraint
        for constraint in schema.rollout_authorizations.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name.endswith("rollout_forward_transition")
    )
    expression = str(transition.sqltext)
    assert "record_only" in expression and "shadow" in expression
    assert "canary" not in expression


def test_descriptor_lifecycle_is_typed_and_dependents_are_version_linked() -> None:
    assert "source_fact_version_id" in schema.initiatives.c
    routes = {
        (method, route.path)
        for route in semantic_router().routes
        for method in route.methods or set()
    }
    assert {
        ("POST", "/v1/facts/{fact_id}/correction-preview"),
        ("POST", "/v1/descriptor-corrections/{transaction_id}/confirm"),
        ("POST", "/v1/facts/{fact_id}/retraction-preview"),
        ("POST", "/v1/descriptor-retractions/{transaction_id}/confirm"),
    } <= routes


def test_fact_axes_and_active_uniqueness_are_predicate_scoped() -> None:
    check_names = {
        constraint.name
        for constraint in schema.fact_versions.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expected_checks = {
        "fact_authority_axis",
        "fact_support_axis",
        "fact_contradiction_axis",
        "fact_freshness_axis",
        "fact_coverage_axis",
        "fact_resolution_axis",
        "fact_privacy_axis",
    }
    assert all(
        any(name.endswith(expected) for name in check_names)
        for expected in expected_checks
    )

    indexes = {index.name: index for index in schema.fact_versions.indexes}
    scalar_where = str(
        indexes["uq_active_scalar_fact"].dialect_options["postgresql"]["where"]
    )
    parent_where = str(
        indexes["uq_active_parent_relationship"].dialect_options["postgresql"]["where"]
    )
    assert "place_social_descriptor" in scalar_where
    assert "parent_of" not in scalar_where
    assert "parent_of" in parent_where
