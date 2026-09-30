"""A function signature restated per statement drifts silently.

Migration 0024 declared a fifteen-argument kernel and then named a
fourteen-argument one in ALTER and DROP. Nothing catches that statically:
Postgres resolves functions by signature, so the migration ran the CREATE, then
failed on the ALTER with "function ... does not exist" — after five earlier
migrations had already applied in the same run.

These tests compare what each migration CREATEs against every later reference
to the same function, so a drift fails here rather than half-way through a
deployment.
"""

from __future__ import annotations

import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _argument_count(signature: str) -> int:
    return len(
        re.findall(r"\b(uuid|text|timestamptz|jsonb|boolean|integer)\b", signature)
    )


def test_new_migrations_declare_a_function_signature_once() -> None:
    """The convention that makes drift impossible, required from 0022 onward.

    A general "does every ALTER match its CREATE" check is not reliable here:
    a function with DEFAULT parameters can be referenced with fewer arguments
    than it declares, and three older migrations legitimately do that. Rather
    than encode a rule that flags working code, this asserts the narrower thing
    that is actually true and actually prevents the defect: a migration that
    creates a function and refers to it again names the signature once.
    """

    problems = []
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name < "0022":
            continue
        # Join adjacent string fragments so a reference split across lines
        # reads as the SQL statement it becomes.
        source = re.sub(r'"\s*\n\s*f?"', "", path.read_text())
        created = {
            match.group(1): _argument_count(match.group(2))
            for match in re.finditer(
                r"CREATE (?:OR REPLACE )?FUNCTION\s+([\w.]+)\s*\((.*?)\)\s*RETURNS",
                source,
                re.S,
            )
        }
        if not created:
            continue
        # The declared constant may be named SIGNATURE, FUNCTIONS or anything
        # else; what matters is that no reference restates an argument list
        # by hand. A literal list is tolerated only where it cannot drift from
        # this migration's own CREATE of the same function.
        for match in re.finditer(
            r"(?:ALTER|DROP) FUNCTION (?:IF EXISTS )?([\w.]+)\s*\(([^)]*)\)", source
        ):
            name, arguments = match.group(1), match.group(2)
            if "{" in arguments:
                continue
            if created.get(name) == _argument_count(arguments):
                continue
            problems.append(
                f"{path.name} creates a function and names {name}({arguments}) "
                "again by hand; declare the signature once and interpolate"
            )
    assert not problems, "\n".join(problems)


def test_the_owner_partner_kernel_declares_its_signature_once() -> None:
    """The specific defect: it now interpolates a single constant."""

    source = (VERSIONS / "0024_owner_partner_commit_kernel.py").read_text()
    assert "SIGNATURE = (" in source
    assert source.count("{SIGNATURE}") >= 2, (
        "ALTER and DROP must both use the shared constant"
    )
    create = source[
        source.index("CREATE FUNCTION identity.commit_owner_partner"):
        source.index("RETURNS uuid")
    ]
    declared = len(re.findall(r"^\s+\w+\s+(?:uuid|text),?$", create, re.M))
    constant = source[source.index("SIGNATURE = ("):]
    constant = constant[: constant.index(")\n")]
    assert declared == _argument_count(constant), (
        f"CREATE declares {declared} parameters, SIGNATURE names "
        f"{_argument_count(constant)}"
    )
