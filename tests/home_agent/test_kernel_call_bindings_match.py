"""Every ``value.<field>`` a kernel call reads must exist on its dataclass.

``POST /v1/household-person`` was unusable in production because
``create_owner_person`` bound ``value.subject_person_id`` and ``value.predicate``
-- fields copied from the *partner* kernel call, which its own dataclass does not
have. The dataclasses use ``slots=True``, so that is an ``AttributeError`` raised
before any SQL runs; and because ``AttributeError`` is not a ``DBAPIError``, the
adapter's retry and error mapping did not catch it either. Adding a person failed
for every caller, every time.

No test caught it because the suites around this code either build the call
object directly (proving the dataclass is fine) or stub the database (proving the
adapter is fine). The defect lived in the seam between them: a real object handed
to a real binding.

This reads the source rather than executing it, so it holds for kernels that need
a live PostgreSQL to run at all.
"""

from __future__ import annotations

import ast
import pathlib

DB = (
    pathlib.Path(__file__).resolve().parents[2]
    / "stack/services/home-agent-core/app/db.py"
)

TREE = ast.parse(DB.read_text(encoding="utf-8"))


def _dataclass_fields() -> dict[str, set[str]]:
    """Field names of every class in db.py, including inherited annotations."""

    fields: dict[str, set[str]] = {}
    for node in ast.walk(TREE):
        if not isinstance(node, ast.ClassDef):
            continue
        names = {
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        }
        fields[node.name] = names
    return fields


def _annotation_name(annotation: ast.expr | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _methods_taking_a_call_object():
    """(method, parameter name, dataclass name) for each kernel call method."""

    for node in ast.walk(TREE):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for argument in node.args.args:
            annotation = _annotation_name(argument.annotation)
            if annotation and annotation.endswith("KernelCall"):
                yield node, argument.arg, annotation


def test_every_kernel_call_field_read_exists_on_its_dataclass() -> None:
    fields = _dataclass_fields()
    checked = 0
    problems: list[str] = []

    for method, parameter, dataclass_name in _methods_taking_a_call_object():
        declared = fields.get(dataclass_name)
        if declared is None:
            problems.append(
                f"{method.name}: annotated {dataclass_name}, which db.py does not define"
            )
            continue
        checked += 1
        for node in ast.walk(method):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == parameter
                and node.attr not in declared
            ):
                problems.append(
                    f"{method.name} reads {parameter}.{node.attr}, "
                    f"but {dataclass_name} has no such field "
                    f"(slots=True makes this an AttributeError at runtime)"
                )

    assert checked > 0, "no kernel call methods found -- has db.py been restructured?"
    assert not problems, "kernel call binding mismatch:\n  " + "\n  ".join(problems)


def test_the_owner_person_call_is_covered_by_that_check() -> None:
    """Name the specific regression, so a rename cannot silently drop it."""

    names = {method.name for method, _, _ in _methods_taking_a_call_object()}
    assert "create_owner_person" in names, (
        "create_owner_person is no longer checked; if it was renamed, update this "
        "test rather than deleting it"
    )
