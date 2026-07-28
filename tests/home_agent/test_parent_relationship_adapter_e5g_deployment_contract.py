from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = "stack/services/home-agent-core/app/db.py"
API_PATH = "stack/services/home-agent-core/app/api.py"
MAIN_PATH = "stack/services/home-agent-core/app/main.py"
MODELS_PATH = "stack/services/home-agent-core/app/models.py"
ADAPTER_PATH = (
    "stack/services/home-agent-core/app/parent_relationship_adapter.py"
)
BFF_PATH = "stack/services/home-agent-bff/src/bff.mjs"
STAGE_FUNCTION = "stage_authenticated_parent_relationship_e5e"
COMMIT_FUNCTION = "commit_authenticated_parent_relationship_e5f"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def parsed(path: str) -> ast.Module:
    return ast.parse(read(path))


def class_node(path: str, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in parsed(path).body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def class_method(
    path: str,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in class_node(path, class_name).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    ]
    assert len(matches) == 1
    return matches[0]


def function(
    path: str,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(parsed(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def source(path: str, node: ast.AST) -> str:
    value = ast.get_source_segment(read(path), node)
    assert value is not None
    return value


def test_e5g_database_pool_is_table_blind_and_kernel_only() -> None:
    database = class_node(DB_PATH, "ParentRelationshipAuthorityDatabase")
    methods = {
        node.name
        for node in database.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"__init__", "stage", "commit", "close"}

    for method_name, kernel_name in (
        ("stage", STAGE_FUNCTION),
        ("commit", COMMIT_FUNCTION),
    ):
        body = source(
            DB_PATH,
            class_method(
                DB_PATH,
                "ParentRelationshipAuthorityDatabase",
                method_name,
            ),
        )
        assert body.count("connection.execute(") == 1
        assert 'isolation_level="SERIALIZABLE"' in body
        assert kernel_name in body
        assert "set_config(" not in body
        assert "SELECT session_user" not in body
        assert "identity.people" not in body
        assert "knowledge.fact_versions" not in body


def test_e5g_core_routes_are_exact_revision_and_identity_gated() -> None:
    api = read(API_PATH)
    assert (
        'PARENT_RELATIONSHIP_ADAPTER_REVISION = "0020_parent_commit_e5f"'
        in api
    )
    routes = (
        (
            "stage_parent_relationship_proposal",
            "/parent-relationship-proposal",
            "ParentRelationshipPreviewRequest.model_validate_json",
            "adapter.stage(",
        ),
        (
            "confirm_parent_relationship_proposal",
            "/parent-relationship-proposal/confirm",
            "ParentRelationshipConfirmation.model_validate_json",
            "adapter.commit(",
        ),
    )
    for name, route_path, validator, call in routes:
        node = function(API_PATH, name)
        body = source(API_PATH, node)
        post_paths = {
            ast.literal_eval(decorator.args[0])
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "post"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        }
        assert post_paths == {route_path}
        assert body.index("readiness_migration") < body.index(
            "raw_body = await request.body()"
        )
        assert validator in body
        assert call in body
        assert "service.ha_user_id" in body
        for forbidden in (
            "parent_person_id",
            "child_person_id",
            "principal_id",
            "actor_id",
        ):
            assert forbidden not in body


def test_e5g_protocol_models_expose_no_semantic_identity_input() -> None:
    model_names = (
        "ParentRelationshipPreviewRequest",
        "ParentRelationshipConfirmation",
    )
    for model_name in model_names:
        model = class_node(MODELS_PATH, model_name)
        annotations = {
            node.target.id
            for node in model.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        }
        assert not annotations.intersection(
            {
                "parent_person_id",
                "child_person_id",
                "person_id",
                "principal_id",
                "ha_user_id",
                "actor_id",
            }
        )
    assert {
        node.target.id
        for node in class_node(
            MODELS_PATH,
            "ParentRelationshipPreviewRequest",
        ).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    } == {"ceremony_id"}
    assert {
        node.target.id
        for node in class_node(
            MODELS_PATH,
            "ParentRelationshipConfirmation",
        ).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    } == {
        "proposal_id",
        "proposal_digest",
        "confirmation_nonce",
    }


def test_e5g_ids_are_server_derived_and_candidate_set_is_kernel_derived() -> None:
    adapter = read(ADAPTER_PATH)
    compact = "".join(adapter.split())
    assert "_derived_uuid7(" in adapter
    assert "_review_code(" in adapter
    assert "authenticated_ha_user_id=ha_user_id" in adapter
    assert "reviewed_display_label=staged.parent_0_display_label" in compact
    assert "reviewed_display_label=staged.parent_1_display_label" in compact
    for forbidden in (
        "parent_person_id=value.",
        "child_person_id=value.",
        "principal_id=value.",
    ):
        assert forbidden not in adapter


def test_e5g_bff_is_browser_only_fresh_and_strict() -> None:
    bff = read(BFF_PATH)
    browser_routes, native_routes = bff.split(
        "const NATIVE_ROUTES",
        1,
    )
    assert "PARENT_RELATIONSHIP_FRESH_AUTH_ROUTES" in bff
    assert "normalizeParentRelationshipBody" in bff
    assert "parent-relationship-proposal" in browser_routes
    assert "parent-relationship-proposal" not in native_routes.split(
        "function randomToken",
        1,
    )[0]
    assert "keys.length !== 1" in bff
    assert "keys.length !== 3" in bff
    assert "forcePrincipalCheck: isFreshIdentityRoute" in bff


def test_e5g_uses_split_credential_and_remains_dormant_in_production() -> None:
    main = read(MAIN_PATH)
    compose = read("stack/home-agent-compose.yml")
    config = read("stack/services/home-agent-core/app/config.py")
    entrypoint = read("stack/services/home-agent-core/docker-entrypoint.sh")

    assert "settings.async_binding_commit_database_url()" in main
    assert "ParentRelationshipAuthorityDatabase(" in main
    assert "AuthenticatedParentRelationshipAdapter(" in main
    assert 'readiness_migration: str = "0006a_worker_lease_arbitration"' in config
    assert 'DEPLOYABLE_MIGRATION_REVISION="0006a_worker_lease_arbitration"' in (
        entrypoint
    )
    assert "0020_parent_commit_e5f" not in compose
