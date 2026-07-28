from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
STAGING_PATH = Path(
    "stack/home-agent-deploy/operator/principal_binding_candidate_staging.py"
)
PRINCIPAL_PATH = Path("stack/services/home-agent-core/app/principal_binding.py")
API_PATH = Path("stack/services/home-agent-core/app/api.py")
STORE_PATH = Path("stack/services/home-agent-core/app/store.py")
E5B_COMMIT_FUNCTION = "commit_authenticated_principal_binding_e5b"


def read(path: str | Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def parse(path: str | Path) -> ast.Module:
    return ast.parse(read(path), filename=str(path))


def function(path: str | Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(parse(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {name} in {path}")
    return matches[0]


def assigned_literal(path: str | Path, name: str) -> object:
    matches = [
        node.value
        for node in parse(path).body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == name
                    for target in node.targets
                )
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
            )
        )
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one assignment to {name} in {path}")
    return ast.literal_eval(matches[0])


def class_definition(path: str | Path, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in parse(path).body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one class {name} in {path}")
    return matches[0]


def dataclass_field_keywords(
    path: str | Path, class_name: str, field_name: str
) -> dict[str, ast.expr]:
    matches = [
        node.value
        for node in class_definition(path, class_name).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == field_name
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "field"
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one dataclass field {class_name}.{field_name}"
        )
    return {
        keyword.arg: keyword.value
        for keyword in matches[0].keywords
        if keyword.arg is not None
    }


def class_annotated_literal(
    path: str | Path, class_name: str, field_name: str
) -> object:
    matches = [
        node.value
        for node in class_definition(path, class_name).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == field_name
        and node.value is not None
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one annotated value {class_name}.{field_name}"
        )
    return ast.literal_eval(matches[0])


def compose_service(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)", source
    )
    if match is None:
        raise AssertionError(f"missing Compose service: {name}")
    return match.group(1)


class PrincipalBindingAuthorityBoundaryContractTests(unittest.TestCase):
    def test_operator_nominee_and_core_preview_share_only_dormant_vocabulary(
        self,
    ) -> None:
        self.assertEqual(
            assigned_literal(STAGING_PATH, "NOMINATION_ROLE"),
            "operator_review_candidate",
        )
        self.assertEqual(
            assigned_literal(STAGING_PATH, "NOMINATION_AUTHORITY_STATUS"),
            "unverified",
        )
        self.assertEqual(
            assigned_literal(STAGING_PATH, "DEPLOYMENT_STATUS"), "non_deployable"
        )
        self.assertEqual(
            assigned_literal(STAGING_PATH, "CAPABILITY_STATUS"), "capability_disabled"
        )
        role = dataclass_field_keywords(
            STAGING_PATH, "ReviewedPrincipalNominee", "nomination_role"
        )
        authority = dataclass_field_keywords(
            STAGING_PATH,
            "ReviewedPrincipalNominee",
            "nomination_authority_status",
        )
        count = dataclass_field_keywords(
            STAGING_PATH, "ReviewedPrincipalNominee", "nominee_count"
        )
        self.assertIsInstance(role["default"], ast.Name)
        self.assertEqual(role["default"].id, "NOMINATION_ROLE")
        self.assertIsInstance(authority["default"], ast.Name)
        self.assertEqual(authority["default"].id, "NOMINATION_AUTHORITY_STATUS")
        self.assertEqual(ast.literal_eval(count["default"]), 1)
        self.assertIs(ast.literal_eval(count["init"]), False)

        validation = function(PRINCIPAL_PATH, "_validate_context")
        literal_requirements = {
            (call.args[0].attr, ast.literal_eval(call.args[1]))
            for call in ast.walk(validation)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_require_literal"
            and len(call.args) >= 2
            and isinstance(call.args[0], ast.Attribute)
            and isinstance(call.args[1], ast.Constant)
        }
        self.assertIn(
            ("nomination_role", "operator_review_candidate"), literal_requirements
        )
        self.assertIn(
            ("nomination_authority_status", "unverified"), literal_requirements
        )
        core_count = dataclass_field_keywords(
            PRINCIPAL_PATH, "PrincipalBindingPreview", "nominee_count"
        )
        self.assertEqual(ast.literal_eval(core_count["default"]), 1)
        self.assertIs(ast.literal_eval(core_count["init"]), False)

    def test_nominee_compiler_cannot_receive_ceremony_authority_inputs(self) -> None:
        compiler = function(STAGING_PATH, "compile_reviewed_principal_nominee")
        parameters = {
            argument.arg
            for argument in (
                *compiler.args.posonlyargs,
                *compiler.args.args,
                *compiler.args.kwonlyargs,
            )
        }
        self.assertEqual(
            parameters,
            {
                "raw_review_bundle",
                "raw_finalization_envelope",
                "source_records",
                "selected_person_projection_receipt_id",
                "policy",
            },
        )
        authority_inputs = {
            "ha_user_id",
            "ha_user_snapshot",
            "principal_id",
            "binding_graph",
            "gesture",
            "confirmation_artifact",
            "cutover_receipt",
            "retrieval_state",
            "erasure_state",
            "commit",
        }
        self.assertTrue(parameters.isdisjoint(authority_inputs))

        locked = function(STAGING_PATH, "_locked_claims")
        returns = [node for node in ast.walk(locked) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        claims = ast.literal_eval(returns[0].value)
        expected_locked_claims = {
            "source_signatures_verified_during_compilation": True,
            "signed_person_projection_verified": True,
            "exact_receipt_selector_resolved": True,
            "signed_snapshot_has_no_archived_nominee": True,
            "signed_snapshot_has_no_blocking_nominee_directive": True,
            "portable_proof": False,
            "downstream_consumption_allowed": False,
            "requires_raw_artifact_reverification": True,
            "operator_identity_verified": False,
            "operator_nomination_authority_verified": False,
            "selector_authority_verified": False,
            "ha_identity_verified": False,
            "binding_graph_verified": False,
            "current_person_status_verified": False,
            "current_privacy_state_verified": False,
            "current_retrieval_state_verified": False,
            "erasure_coverage_complete": False,
            "fresh_confirmation_verified": False,
            "single_use_artifact_consumed": False,
            "serializable_commit_enforced": False,
            "me_identity_established": False,
            "binding_created": False,
            "authoritative": False,
            "commit_ready": False,
            "enables_writes": False,
            "location_memory_enabled": False,
            "travel_greetings_enabled": False,
        }
        self.assertEqual(claims, expected_locked_claims)
        for field, expected in expected_locked_claims.items():
            keywords = dataclass_field_keywords(
                STAGING_PATH, "ReviewedPrincipalNominee", field
            )
            self.assertIs(ast.literal_eval(keywords["default"]), expected, field)
            self.assertIs(ast.literal_eval(keywords["init"]), False, field)

        imports = {
            alias.name
            for node in ast.walk(parse(STAGING_PATH))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports |= {
            node.module or ""
            for node in ast.walk(parse(STAGING_PATH))
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(
            any(
                name.startswith(
                    (
                        "psycopg",
                        "sqlalchemy",
                        "fastapi",
                        "requests",
                        "socket",
                        "subprocess",
                    )
                )
                for name in imports
            )
        )
        dynamic_calls = {
            node.func.id
            for node in ast.walk(parse(STAGING_PATH))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(dynamic_calls.isdisjoint({"__import__", "eval", "exec"}))

    def test_operator_nominee_has_no_runtime_import_or_adapter(self) -> None:
        runtime_roots = (
            ROOT / "stack/services/home-agent-core/app",
            ROOT / "stack/services/home-agent-bff",
            ROOT / "app/src",
            ROOT / "app/src-tauri/src",
            ROOT / "web-gateway",
            ROOT / "ha-config/home_agent_edge",
        )
        suffixes = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".rs"}
        for root in runtime_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("principal_binding_candidate_staging", source, path)
                self.assertNotIn("ReviewedPrincipalNominee", source, path)

        compose = read("stack/home-agent-compose.yml")
        self.assertNotIn("principal_binding_candidate_staging", compose)
        self.assertNotIn("ReviewedPrincipalNominee", compose)

    def test_public_confirmation_route_is_revision_gated_before_body_parse(
        self,
    ) -> None:
        route = function(API_PATH, "principal_binding_confirmation")
        parameters = [
            argument.arg
            for argument in (
                *route.args.posonlyargs,
                *route.args.args,
                *route.args.kwonlyargs,
            )
        ]
        self.assertEqual(parameters, ["request", "service"])
        post_paths = {
            ast.literal_eval(decorator.args[0])
            for decorator in route.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "post"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        }
        self.assertEqual(post_paths, {"/principal-binding-proposal/confirm"})
        source = ast.get_source_segment(read(API_PATH), route)
        assert source is not None
        self.assertLess(
            source.index("readiness_migration"),
            source.index("raw_body = await request.body()"),
        )
        self.assertIn("PRINCIPAL_BINDING_CONFIRMATION_RETIRED", source)
        self.assertIn("PrincipalBindingConfirmation.model_validate_json", source)
        self.assertIn("store.principal_binding_commit_context(", source)
        self.assertIn("adapter.commit(", source)
        self.assertNotIn("confirm_principal_binding_proposal(", source)

    def test_runtime_deploy_pin_and_finalizer_surface_remain_disabled(self) -> None:
        revision = "0006a_worker_lease_arbitration"
        self.assertEqual(
            class_annotated_literal(
                "stack/services/home-agent-core/app/config.py",
                "Settings",
                "readiness_migration",
            ),
            revision,
        )
        self.assertEqual(assigned_literal(API_PATH, "PHASE3_SCHEMA_REVISION"), revision)
        entrypoint = read("stack/services/home-agent-core/docker-entrypoint.sh")
        deployable_assignments = [
            line.strip()
            for line in entrypoint.splitlines()
            if re.fullmatch(
                r"(?:export\s+)?DEPLOYABLE_MIGRATION_REVISION=.*", line.strip()
            )
        ]
        self.assertEqual(
            deployable_assignments,
            [f'DEPLOYABLE_MIGRATION_REVISION="{revision}"'],
        )

        finalizer = compose_service(
            read("stack/home-agent-compose.yml"), "identity-finalizer"
        )
        command_lines = [
            line.strip()
            for line in finalizer.splitlines()
            if line.strip().startswith("command:")
        ]
        self.assertEqual(
            command_lines,
            [
                "command: [\"echo 'identity finalizer kernel is dormant and not "
                'activated\' >&2; exit 78"]'
            ],
        )
        entrypoint_lines = [
            line.strip()
            for line in finalizer.splitlines()
            if line.strip().startswith("entrypoint:")
        ]
        self.assertEqual(entrypoint_lines, ['entrypoint: ["/bin/sh", "-c"]'])
        self.assertIn("profiles: [operator]", finalizer)
        self.assertIn('restart: "no"', finalizer)

    def test_e5c_runtime_adapter_is_narrow_and_has_no_native_or_bff_expansion(
        self,
    ) -> None:
        runtime_roots = (
            ROOT / "stack/services/home-agent-core/app",
            ROOT / "stack/services/home-agent-bff",
            ROOT / "app/src",
            ROOT / "app/src-tauri/src",
            ROOT / "web-gateway",
            ROOT / "ha-config/home_agent_edge",
        )
        suffixes = {
            ".html",
            ".js",
            ".json",
            ".jsx",
            ".mjs",
            ".py",
            ".rs",
            ".sh",
            ".sql",
            ".toml",
            ".ts",
            ".tsx",
            ".yaml",
            ".yml",
        }
        forbidden_client_tokens = (
            E5B_COMMIT_FUNCTION,
            "home_agent_identity_binding_kernel",
            "IDENTITY_BINDING_KERNEL_DATABASE_URL",
            "PRINCIPAL_BINDING_KERNEL_E5B",
        )
        for root in runtime_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                source = path.read_text(encoding="utf-8")
                if path == ROOT / "stack/services/home-agent-core/app/db.py":
                    self.assertEqual(source.count(E5B_COMMIT_FUNCTION), 1)
                    continue
                if path == API_PATH:
                    self.assertNotIn(E5B_COMMIT_FUNCTION, source, path)
                    continue
                for token in forbidden_client_tokens:
                    self.assertNotIn(token, source, path)

        compose = read("stack/home-agent-compose.yml")
        environment = read("stack/home-agent.env.example")
        self.assertNotIn(E5B_COMMIT_FUNCTION, compose)
        self.assertNotIn("home_agent_identity_binding_kernel", compose)
        self.assertNotIn("IDENTITY_BINDING_KERNEL", compose)
        self.assertNotIn("IDENTITY_BINDING_KERNEL", environment)
        self.assertIn("binding_commit_database_url", compose)
        self.assertNotIn("principal-binding-proposal/confirm", compose)

    def test_legacy_self_confirming_store_has_no_non_test_static_reference(
        self,
    ) -> None:
        references: list[str] = []
        method_name = "confirm_principal_binding_proposal"
        for path in ROOT.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative == STORE_PATH or "tests" in relative.parts:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            if method_name in source:
                references.append(str(relative))
        self.assertEqual(references, [])


if __name__ == "__main__":
    unittest.main()
