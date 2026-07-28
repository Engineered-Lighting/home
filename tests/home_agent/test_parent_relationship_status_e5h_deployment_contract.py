from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = (ROOT / "stack/services/home-agent-core/app/api.py").read_text(encoding="utf-8")
MODELS = (ROOT / "stack/services/home-agent-core/app/models.py").read_text(
    encoding="utf-8"
)
ADAPTER = (
    ROOT / "stack/services/home-agent-core/app/parent_relationship_adapter.py"
).read_text(encoding="utf-8")
CLIENT = (ROOT / "app/src/home-agent/api.js").read_text(encoding="utf-8")
PANEL = (ROOT / "app/src/home-agent/panel.jsx").read_text(encoding="utf-8")
BFF = (ROOT / "stack/services/home-agent-bff/src/bff.mjs").read_text(encoding="utf-8")


def test_e5h_onboarding_exposes_only_a_content_free_capability_bit() -> None:
    model = next(
        node
        for node in ast.parse(MODELS).body
        if isinstance(node, ast.ClassDef) and node.name == "OnboardingStatusView"
    )
    fields = {
        node.target.id
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "parent_relationship_confirmation" in fields
    for private_field in (
        "parent_name",
        "parent_person_id",
        "proposal_id",
        "proposal_digest",
        "review_code",
    ):
        assert private_field not in fields
    assert 'parent_relationship_confirmation: Literal["disabled", "enabled"]' in MODELS
    assert 'PARENT_RELATIONSHIP_ADAPTER_REVISION = "0021_parent_status_e5h"' in API


def test_e5h_browser_recovers_status_without_private_client_storage() -> None:
    assert "parentRelationshipStatus()" in CLIENT
    assert 'this.request("/api/agent/v1/parent-relationship-proposal")' in CLIENT
    assert "stageParentRelationship()" in CLIENT
    assert "confirmParentRelationship(proposalId, proposalDigest)" in CLIENT
    assert "randomUuid7()" in CLIENT
    assert "global.crypto.randomUUID()" in CLIENT
    combined = CLIENT + PANEL
    assert "localStorage" not in combined
    assert "sessionStorage" not in combined
    assert "native_parent_relationship_unavailable" in CLIENT


def test_e5h_private_ui_is_explicit_atomic_and_location_safe() -> None:
    for text in (
        "Private relationship review",
        "Review parent relationships",
        "Confirm both parent relationships",
        "creates exactly two private",
        "does not assert ownership, residence, current presence",
        "Location memory",
        "Travel greetings",
    ):
        assert text in PANEL
    assert 'nextOnboarding?.parent_relationship_confirmation === "enabled"' in PANEL
    assert "await api.parentRelationshipStatus()" in PANEL
    assert "await api.confirmParentRelationship(" in PANEL
    assert 'new Set(["rollout_contained", "ready"]).has(phase)' in PANEL


def test_e5h_status_route_is_browser_only_and_freshly_reauthenticated() -> None:
    browser, native = BFF.split("const NATIVE_ROUTES", 1)
    native_catalog = native.split("function randomToken", 1)[0]
    assert '["GET", /^\\/api\\/agent\\/v1\\/parent-relationship-proposal$/]' in (
        browser
    )
    assert "parent-relationship-proposal" not in native_catalog
    assert "`GET ${PARENT_RELATIONSHIP_STAGE_PATH}`" in BFF
    assert "forcePrincipalCheck: isFreshIdentityRoute" in BFF


def test_e5h_adapter_returns_no_names_after_confirmation() -> None:
    confirmed_branch = ADAPTER.split(
        'if recovered.state == "confirmed":',
        1,
    )[1].split('if recovered.state == "not_started":', 1)[0]
    assert "confirmed_at=recovered.confirmed_at" in confirmed_branch
    for private_value in (
        "parent_0_display_label",
        "parent_1_display_label",
        "proposal_digest",
        "child_display_label",
    ):
        assert private_value not in confirmed_branch
