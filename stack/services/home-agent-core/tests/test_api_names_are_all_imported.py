"""Every model name api.py uses must be imported there.

``app/api.py`` referenced OwnerPersonView, OwnerPersonCreate,
OwnerPartnerAttestationView and OwnerPartnerAttestation without importing any of
them. The names are used inside ``semantic_router()``, so ``import app.api``
succeeds and nothing complains until the router is actually built -- at which
point the process raises NameError and core-api does not serve.

That is exactly how it reached the deployment: the import error was patched by
hand on the deployment root during an outage and never committed, so the
deployed machine worked while main stayed broken. Anyone deploying main would
have reproduced the outage.

This check is static and needs no database, no fastapi, and no import of the
module under test: it compares the names api.py uses against the classes
models.py defines, and fails on any that are used without being imported. A
test that merely imported app.api would have passed while the defect was live.
"""

from __future__ import annotations

import ast
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _imported_from_models(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("models"):
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def _model_classes() -> set[str]:
    source = (APP / "models.py").read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^class (\w+)", source, re.M)}


def test_api_imports_every_model_it_references() -> None:
    source = (APP / "api.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    missing = sorted((used & _model_classes()) - _imported_from_models(tree))

    assert not missing, (
        "api.py uses these models without importing them: "
        f"{missing}. They are referenced inside a function, so importing the "
        "module still succeeds -- the failure appears only when the router is "
        "built, and core-api stops serving."
    )


def test_every_module_under_app_imports_the_names_it_uses() -> None:
    """The same class of defect, anywhere in the service.

    Scoped to names models.py defines, so it stays quiet about builtins,
    locals and anything imported by another route.
    """

    classes = _model_classes()
    problems = []
    for path in sorted(APP.glob("*.py")):
        if path.name == "models.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_from_models(tree)
        assigned = {
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        }
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        missing = sorted((used & classes) - imported - assigned)
        if missing:
            problems.append(f"{path.name}: {missing}")
    assert not problems, "\n".join(problems)
